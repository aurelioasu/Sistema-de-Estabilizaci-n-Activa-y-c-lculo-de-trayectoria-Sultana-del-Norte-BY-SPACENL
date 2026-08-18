from __future__ import annotations

from app.services.serial_telemetry import SerialTelemetryClient, parse_telemetry_line


class FakeSerial:
    def __init__(self, **_kwargs: object) -> None:
        self.lines = [
            b'{"time_s": 1.5, "altitude_agl_m": 42.0, "c1": -3}\n',
            b'tiempo=1.6,altura=43.2,mach=0.18,paracaidas=no\n',
        ]
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        return sum(len(line) for line in self.lines)

    def readline(self) -> bytes:
        return self.lines.pop(0)

    def read(self, count: int) -> bytes:
        data = b"".join(self.lines)
        self.lines.clear()
        return data[:count]

    def close(self) -> None:
        self.is_open = False


def test_parser_accepts_json_and_esp_key_values() -> None:
    json_packet = parse_telemetry_line('{"time_s": 2, "altitude_agl_m": 10, "c1": -4}')
    text_packet = parse_telemetry_line("tiempo=2.1,altura=11.5,estado=ascenso,paracaidas=no")
    assert json_packet["canard1_deg"] == -4
    assert text_packet == {
        "time_s": 2.1,
        "altitude_agl_m": 11.5,
        "state": "ascenso",
        "parachute_deployed": False,
    }


def test_nonblocking_client_reads_available_packets() -> None:
    fake = FakeSerial()
    client = SerialTelemetryClient(factory=lambda **_kwargs: fake)
    client.open("COM9", 115200)
    packets, errors = client.read_available()
    assert not errors
    assert [packet["time_s"] for packet in packets] == [1.5, 1.6]
    assert packets[1]["altitude_agl_m"] == 43.2
    client.close()
    assert not client.is_open
