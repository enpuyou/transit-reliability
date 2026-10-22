// A small reader for the agency's bus-position file (GTFS-realtime protobuf), the same logic as site/gtfsrt.js but
// keeping only what the trip memory needs: which trips are running, on which bus, and where they last reported.

export interface Running {
  tripId: string;
  routeId: string | null;
  vehicleId: string | null;
  ts: number | null;
  lat: number | null;
  lon: number | null;
}

class Reader {
  p = 0;
  private dv: DataView;
  constructor(public b: Uint8Array) {
    this.dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  }
  varint(): number {
    let lo = 0, mul = 1, byte: number;
    do {
      byte = this.b[this.p++];
      lo += (byte & 0x7f) * mul;
      mul *= 128;
    } while (byte & 0x80);
    return lo;
  }
  skip(wire: number): void {
    if (wire === 0) this.varint();
    else if (wire === 1) this.p += 8;
    else if (wire === 2) {
      const n = this.varint();
      this.p += n;
    } else if (wire === 5) this.p += 4;
    else throw new Error(`unsupported protobuf wire type ${wire}`);
  }
  float(): number {
    const v = this.dv.getFloat32(this.p, true);
    this.p += 4;
    return v;
  }
  string(len: number): string {
    const s = new TextDecoder().decode(this.b.subarray(this.p, this.p + len));
    this.p += len;
    return s;
  }
}

function each(r: Reader, end: number, onField: (field: number, wire: number) => boolean): void {
  while (r.p < end) {
    const tag = r.varint(), field = Math.floor(tag / 8), wire = tag % 8;
    if (!onField(field, wire)) r.skip(wire);
  }
}

/** Every bus in the file that is on a trip, with where and when it last reported. */
export function runningTrips(bytes: Uint8Array): { timestamp: number | null; trips: Running[] } {
  const r = new Reader(bytes);
  let timestamp: number | null = null;
  const trips: Running[] = [];
  each(r, bytes.length, (f, w) => {
    if (w !== 2) return false;
    const len = r.varint(), stop = r.p + len;
    if (f === 1) {
      each(r, stop, (hf, hw) => {
        if (hw === 0 && hf === 3) { timestamp = r.varint(); return true; }
        return false;
      });
    } else if (f === 2) {
      each(r, stop, (ef, ew) => {
        if (ew !== 2 || ef !== 4) return false;
        const vlen = r.varint(), vend = r.p + vlen;
        const v: Running = { tripId: "", routeId: null, vehicleId: null, ts: null, lat: null, lon: null };
        each(r, vend, (vf, vw) => {
          if (vw === 2) {
            const l = r.varint(), e = r.p + l;
            if (vf === 1) {
              each(r, e, (tf, tw) => {
                if (tw === 2 && (tf === 1 || tf === 5)) { const s = r.string(r.varint()); if (tf === 1) v.tripId = s; else v.routeId = s; return true; }
                return false;
              });
            } else if (vf === 2) {
              each(r, e, (pf, pw) => {
                if (pw !== 5) return false;
                const x = r.float();
                if (pf === 1) v.lat = x; else if (pf === 2) v.lon = x;
                return true;
              });
            } else if (vf === 8) {
              each(r, e, (df, dw) => {
                if (dw === 2 && df === 1) { v.vehicleId = r.string(r.varint()); return true; }
                return false;
              });
            }
            r.p = e;
            return true;
          }
          if (vw === 0 && vf === 5) { v.ts = r.varint(); return true; }
          return false;
        });
        if (v.tripId) trips.push(v);
        r.p = vend;
        return true;
      });
    }
    r.p = stop;
    return true;
  });
  return { timestamp, trips };
}
