from pathlib import Path

from google.transit import gtfs_realtime_pb2

FIXTURE_NAMES = ("vehiclepositions", "tripupdates", "alerts")


def test_gtfs_realtime_fixtures_decode() -> None:
    for name in FIXTURE_NAMES:
        message = gtfs_realtime_pb2.FeedMessage()
        payload = Path(f"tests/fixtures/{name}.pb").read_bytes()

        assert message.ParseFromString(payload) == len(payload)
        assert message.IsInitialized()
        assert message.entity
