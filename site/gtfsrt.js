/* A tiny reader for the agency's live bus-position file (GTFS-realtime, a binary "protobuf" format).
   It reads only the fields the live map needs and skips the rest. Works in the browser (window.GtfsRt)
   and in Node (module.exports) so it can be tested against the saved sample file. */
(function (root) {
  "use strict";

  function Reader(buf) { this.b = buf; this.p = 0; this.dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength); }
  Reader.prototype.more = function (end) { return this.p < end; };
  Reader.prototype.varint = function () {
    var lo = 0, mul = 1, byte;
    do { byte = this.b[this.p++]; lo += (byte & 0x7f) * mul; mul *= 128; } while (byte & 0x80);
    return lo; // exact up to 2^53, plenty for ids, seconds and counts
  };
  /* Signed 64-bit varint (a negative number takes 10 bytes). Exact for the small values we meet. */
  Reader.prototype.svarint = function () {
    var start = this.p, n = 0;
    while (this.b[this.p++] & 0x80) n++;
    if (n < 6) { var save = this.p; this.p = start; var v = this.varint(); this.p = save; return v; }
    var big = 0n, shift = 0n;
    for (var i = start; i <= start + n; i++) { big |= BigInt(this.b[i] & 0x7f) << shift; shift += 7n; }
    return Number(BigInt.asIntN(64, big));
  };
  Reader.prototype.skip = function (wire) {
    if (wire === 0) this.varint();
    else if (wire === 1) this.p += 8;
    else if (wire === 2) { var n = this.varint(); this.p += n; }
    else if (wire === 5) this.p += 4;
    else throw new Error("unsupported protobuf wire type " + wire);
  };
  Reader.prototype.float = function () { var v = this.dv.getFloat32(this.p, true); this.p += 4; return v; };
  Reader.prototype.string = function (len) {
    var s = new TextDecoder().decode(this.b.subarray(this.p, this.p + len)); this.p += len; return s;
  };

  function each(r, end, onField) {
    while (r.more(end)) {
      var tag = r.varint(), field = Math.floor(tag / 8), wire = tag % 8;
      if (!onField(field, wire)) r.skip(wire);
    }
  }

  function readTrip(r, end, v) {
    each(r, end, function (f, w) {
      if (w === 2 && (f === 1 || f === 3 || f === 5)) {
        var s = r.string(r.varint());
        if (f === 1) v.tripId = s; else if (f === 3) v.startDate = s; else v.routeId = s;
        return true;
      }
      if (w === 0 && f === 6) { v.directionId = r.varint(); return true; }
      return false;
    });
  }
  function readPosition(r, end, v) {
    each(r, end, function (f, w) {
      if (w !== 5) return false;
      var x = r.float();
      if (f === 1) v.lat = x; else if (f === 2) v.lon = x; else if (f === 3) v.bearing = x; else if (f === 5) v.speed = x;
      else return true;
      return true;
    });
  }
  function readDescriptor(r, end, v) {
    each(r, end, function (f, w) {
      if (w === 2 && (f === 1 || f === 2)) { var s = r.string(r.varint()); if (f === 1) v.id = s; else v.label = s; return true; }
      return false;
    });
  }
  function readVehicle(r, end) {
    var v = { id: null, label: null, tripId: null, routeId: null, directionId: null, startDate: null,
      lat: null, lon: null, bearing: null, speed: null, stopSeq: null, status: null, ts: null, stopId: null, occupancy: null };
    each(r, end, function (f, w) {
      if (w === 2) {
        var len = r.varint(), stop = r.p + len;
        if (f === 1) readTrip(r, stop, v);
        else if (f === 2) readPosition(r, stop, v);
        else if (f === 8) readDescriptor(r, stop, v);
        else if (f === 7) { v.stopId = r.string(len); return true; }
        else r.p = stop;
        r.p = stop;
        return true;
      }
      if (w === 0) {
        var n = r.varint();
        if (f === 3) v.stopSeq = n; else if (f === 4) v.status = n; else if (f === 5) v.ts = n; else if (f === 9) v.occupancy = n;
        return true;
      }
      return false;
    });
    return v;
  }
  function readEntity(r, end) {
    var vehicle = null;
    each(r, end, function (f, w) {
      if (w === 2 && f === 4) { var len = r.varint(); vehicle = readVehicle(r, r.p + len); return true; }
      return false;
    });
    return vehicle;
  }
  function readHeader(r, end) {
    var ts = null;
    each(r, end, function (f, w) { if (w === 0 && f === 3) { ts = r.varint(); return true; } return false; });
    return ts;
  }

  /** Decode a vehicle-position file. Returns { timestamp (unix seconds), vehicles: [...] }. */
  function decodeVehicles(bytes) {
    var r = new Reader(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)), out = { timestamp: null, vehicles: [] };
    each(r, bytes.length === undefined ? bytes.byteLength : bytes.length, function (f, w) {
      if (w !== 2) return false;
      var len = r.varint(), stop = r.p + len;
      if (f === 1) out.timestamp = readHeader(r, stop);
      else if (f === 2) { var v = readEntity(r, stop); if (v) out.vehicles.push(v); }
      r.p = stop;
      return true;
    });
    return out;
  }

  function readEvent(r, end) {
    var e = { delay: null, time: null };
    each(r, end, function (f, w) {
      if (w === 0 && f === 1) { e.delay = r.svarint(); return true; }
      if (w === 0 && f === 2) { e.time = r.svarint(); return true; }
      return false;
    });
    return e;
  }
  function readStopUpdate(r, end) {
    var u = { stopSeq: null, stopId: null, arrTime: null, arrDelay: null, depTime: null, depDelay: null };
    each(r, end, function (f, w) {
      if (w === 0 && f === 1) { u.stopSeq = r.varint(); return true; }
      if (w === 2) {
        var len = r.varint(), stop = r.p + len;
        if (f === 2) { var a = readEvent(r, stop); u.arrTime = a.time; u.arrDelay = a.delay; }
        else if (f === 3) { var d = readEvent(r, stop); u.depTime = d.time; u.depDelay = d.delay; }
        else if (f === 4) u.stopId = r.string(len);
        r.p = stop;
        return true;
      }
      return false;
    });
    return u;
  }
  function readTripUpdate(r, end) {
    var t = { tripId: null, routeId: null, startDate: null, directionId: null, updates: [], timestamp: null };
    each(r, end, function (f, w) {
      if (w === 2) {
        var len = r.varint(), stop = r.p + len;
        if (f === 1) readTrip(r, stop, t);
        else if (f === 2) t.updates.push(readStopUpdate(r, stop));
        r.p = stop;
        return true;
      }
      if (w === 0 && f === 4) { t.timestamp = r.varint(); return true; }
      return false;
    });
    return t;
  }

  /** Find one trip's predicted stop times in the agency's trip-update file (null if it has none). */
  function findTripUpdate(bytes, tripId) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes), r = new Reader(u8), found = null, header = null;
    each(r, u8.length, function (f, w) {
      if (w !== 2) return false;
      var len = r.varint(), stop = r.p + len;
      if (f === 1) header = readHeader(r, stop);
      else if (f === 2 && !found) {
        each(r, stop, function (ef, ew) {
          if (ew === 2 && ef === 3) {
            var l2 = r.varint(), s2 = r.p + l2, t = readTripUpdate(r, s2);
            if (t.tripId === tripId) found = t;
            r.p = s2;
            return true;
          }
          return false;
        });
      }
      r.p = stop;
      return true;
    });
    if (found) found.feedTimestamp = header;
    return found;
  }

  /** Every predicted arrival at one stop in the agency's trip-update file:
      [{ tripId, routeId, directionId, time (unix s), delay (s, or null) }] sorted by time. */
  function arrivalsAtStop(bytes, stopId) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes), r = new Reader(u8), out = [], header = null;
    each(r, u8.length, function (f, w) {
      if (w !== 2) return false;
      var len = r.varint(), stop = r.p + len;
      if (f === 1) header = readHeader(r, stop);
      else if (f === 2) {
        each(r, stop, function (ef, ew) {
          if (ew === 2 && ef === 3) {
            var l2 = r.varint(), s2 = r.p + l2, t = readTripUpdate(r, s2);
            t.updates.forEach(function (u) {
              if (u.stopId === stopId && (u.arrTime || u.depTime)) {
                out.push({ tripId: t.tripId, routeId: t.routeId, directionId: t.directionId, time: u.arrTime || u.depTime, delay: u.arrTime ? u.arrDelay : u.depDelay });
              }
            });
            r.p = s2;
            return true;
          }
          return false;
        });
      }
      r.p = stop;
      return true;
    });
    out.sort(function (a, b) { return a.time - b.time; });
    return { feedTimestamp: header, arrivals: out };
  }

  var api = { decodeVehicles: decodeVehicles, findTripUpdate: findTripUpdate, arrivalsAtStop: arrivalsAtStop };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.GtfsRt = api;
})(typeof window !== "undefined" ? window : globalThis);
