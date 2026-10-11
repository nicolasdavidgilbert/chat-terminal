import unittest

from chat_terminal.protocol import Event, IAC, NAWS, Protocol, SB, SE, SSH_ADDRESS, TelnetFilter


class ProtocolTests(unittest.TestCase):
    def test_standalone_escape_and_split_navigation(self):
        parser = Protocol()
        now = [0.0]
        parser.clock = lambda: now[0]
        self.assertEqual(parser.feed(b"\x1b"), [])
        now[0] = 0.099
        self.assertEqual(parser.expire_escape(), [])
        now[0] = 0.1
        self.assertEqual(parser.expire_escape(), [Event("key", "escape")])
        self.assertEqual(parser.expire_escape(), [])
        parser.feed(b"\x1b[")
        now[0] += 1
        self.assertEqual(parser.expire_escape(), [])
        self.assertEqual(parser.feed(b"6~"), [Event("key", "page_down")])
        self.assertEqual(parser.feed(b"\x1b[5~"), [Event("key", "page_up")])
        parser.feed(b"\x1b[200~\x1b")
        now[0] += 1
        self.assertEqual(parser.expire_escape(), [])
        self.assertEqual(parser.feed(b"[201~"), [Event("paste", "")])
    def test_ssh_address_metadata_handles_split_ipv4_ipv6_and_invalid_input(self):
        for address in ("192.168.1.166", "2001:db8::1"):
            parser = Protocol()
            packet = bytes((IAC, SB, SSH_ADDRESS)) + address.encode() + bytes((IAC, SE))
            events = []
            for byte in packet:
                events.extend(parser.feed(bytes((byte,))))
            self.assertEqual(events, [Event("ssh_address", address)])
        for address in (b"ubuntu", b"\xff\xff", b"192.168.1.166\x1b[2J", b"x" * 10000):
            packet = bytes((IAC, SB, SSH_ADDRESS)) + address + bytes((IAC, SE))
            self.assertEqual(Protocol().feed(packet), [])

    def test_split_utf8_and_keys_preserve_order(self):
        protocol = Protocol()
        events = []
        for byte in "á\x1b[D!\r\nsegunda\r".encode():
            events.extend(protocol.feed(bytes((byte,))))
        self.assertEqual(events[:4], [Event("text", "á"), Event("key", "left"),
                                      Event("text", "!"), Event("key", "enter")])
        self.assertEqual(sum(e == Event("key", "enter") for e in events), 2)

    def test_naws_split_and_escaped_iac(self):
        protocol = Protocol()
        packet = bytes((IAC, SB, NAWS, 0, IAC, IAC, 0, 24, IAC, SE))
        events = []
        for byte in packet:
            events.extend(protocol.feed(bytes((byte,))))
        self.assertEqual(events, [Event("resize", (255, 24))])

    def test_paste_never_submits_and_is_bounded(self):
        protocol = Protocol()
        payload = b"\x1b[200~/msg ana hola\r\nexit\tfin\x03\x1b[201~"
        events = []
        for byte in payload:
            events.extend(protocol.feed(bytes((byte,))))
        self.assertEqual(events, [Event("paste", "/msg ana hola  exit fin")])
        events = protocol.feed(b"\x1b[200~" + b"x" * 10000 + b"\x1b[201~")
        self.assertEqual(len(events[0].value), 400)
        self.assertTrue(events[0].truncated)
        events = protocol.feed(b"\x1b[200~ok\x1b[201~")
        self.assertEqual(events, [Event("paste", "ok")])
        protocol.reset()
        self.assertEqual(protocol.paste, bytearray())

    def test_truncated_paste_does_not_create_broken_utf8_character(self):
        protocol = Protocol()
        events = protocol.feed(b"\x1b[200~" + b"x" * 399 + "á".encode() + b"\x1b[201~")
        self.assertEqual(events, [Event("paste", "x" * 399, truncated=True)])

    def test_filter_preserves_data_with_split_negotiation(self):
        parser = TelnetFilter()
        packet = b"hola" + bytes((IAC, 251, 1)) + b" mundo" + bytes((IAC, IAC))
        out = b"".join(parser.feed(bytes((byte,))) for byte in packet)
        self.assertEqual(out, b"hola mundo\xff")

    def test_cr_nul_lf_and_unsupported_options(self):
        protocol = Protocol()
        self.assertEqual(protocol.feed(b"\r\x00\n"), [Event("key", "enter")])
        events = protocol.feed(bytes((IAC, 251, 99)))
        self.assertEqual(events, [Event("reply", bytes((IAC, 254, 99)))])
        protocol.feed(bytes((IAC, SB)) + b"x" * 10000)
        self.assertLessEqual(len(protocol.sub), 64)

    def test_osc_input_and_search_shortcut_are_ignored(self):
        protocol = Protocol()
        events = protocol.feed(b"\x1b]0;titulo\x07\x12hola")
        self.assertEqual(events, [Event("text", ch) for ch in "hola"])
        events = protocol.feed("\x1b[200~👨‍👩‍👧\x1b[201~".encode())
        self.assertEqual(events, [Event("paste", "👨‍👩‍👧")])

    def test_removed_cut_and_yank_shortcuts_are_ignored(self):
        self.assertEqual(Protocol().feed(b"\x15\x0b\x17\x19\x1b\x7f"), [])

    def test_alt_word_movement_is_removed(self):
        protocol = Protocol()
        self.assertEqual(protocol.feed(b"\x1bb\x1bf\x1b[1;3C\x1b[1;3D"), [])
        self.assertEqual(protocol.feed(b"\x1b[1;5C"), [Event("key", "word_right")])
