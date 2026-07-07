"""`claude-fleet` command dispatcher.

    claude-fleet                 launch the widget (default)
    claude-fleet agent           run the headless cross-machine sync loop
    claude-fleet init-room       create a room (needs Upstash creds) -> prints ID + PASS
    claude-fleet link            link this machine to an existing room (ID + PASS)
    claude-fleet unlink          remove remote config (back to local-only)
    claude-fleet status          show remote config + connectivity
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys


def cmd_widget(_args) -> None:
    from . import widget
    widget.main()


def cmd_agent(_args) -> None:
    from . import remote
    cfg = remote.load_remote_cfg()
    if not cfg:
        print("No room configured. Run  claude-fleet init-room  or  claude-fleet link")
        return
    print(f"Sync agent running for room '{cfg['room']}' as '{remote.local_host()}'. Ctrl-C to stop.")
    try:
        remote.run_sync()
    except KeyboardInterrupt:
        print("\nstopped")


def cmd_init_room(args) -> None:
    from . import remote
    url = args.url or os.environ.get("UPSTASH_REDIS_REST_URL") or input("Upstash Redis REST URL: ").strip()
    token = (args.token or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
             or getpass.getpass("Upstash Redis REST TOKEN: ").strip())
    room = args.room or secrets.token_urlsafe(6)
    passphrase = args.passphrase or secrets.token_urlsafe(9)
    cfg = {"url": url.rstrip("/"), "token": token, "room": room, "passphrase": passphrase}

    ok, msg = remote.ping(cfg)
    if not ok and not args.force:
        print(f"! Could not reach the relay: {msg}")
        print("  Check the URL/token, or pass --force to save anyway.")
        return

    remote.save_remote_cfg(cfg)
    code = remote.make_pairing_code(cfg["url"], token, room)
    print("\n=== Room created — this machine is linked ===")
    print("Short ID to share with your other laptops:\n")
    print(f"  ID (room) : {room}")
    print(f"  PASS      : {passphrase}\n")
    print("On another laptop — one-time relay setup, then join with the short ID:")
    print(f"  claude-fleet set-relay --url {cfg['url']} --token {token}")
    print(f"  claude-fleet join --room {room} --pass {passphrase}\n")
    print("Or, one self-contained code (no separate relay step):")
    print(f"  claude-fleet link --code {code} --pass {passphrase}")
    print("\nThen run  claude-fleet  (widget) or  claude-fleet agent  (headless) on every machine.")
    if not ok:
        print(f"\n(note: relay was unreachable during setup: {msg})")


def cmd_set_relay(args) -> None:
    from . import remote
    url = args.url or input("Upstash Redis REST URL: ").strip()
    token = args.token or getpass.getpass("Upstash Redis REST TOKEN: ").strip()
    remote.update_cfg(url=url.rstrip("/"), token=token)
    print("Relay credentials saved. Now join a room:  claude-fleet join")


def cmd_join(args) -> None:
    from . import remote
    cfg = remote._load_raw()
    if not (cfg.get("url") and cfg.get("token")):
        print("No relay configured on this machine.")
        print("Run  claude-fleet set-relay --url <URL> --token <TOKEN>  first,")
        print("or use the self-contained  claude-fleet link --code <CODE> --pass <PASS>.")
        return
    room = args.room or input("Room ID: ").strip()
    passphrase = args.passphrase or getpass.getpass("Passphrase (PASS): ").strip()
    cfg = remote.update_cfg(room=room, passphrase=passphrase)
    ok, msg = remote.ping(cfg)
    print(f"Joined room '{room}' as '{remote.local_host()}'."
          + ("" if ok else f"  (warning: relay unreachable: {msg})"))
    print("Run  claude-fleet  (widget) or  claude-fleet agent  (headless) to start syncing.")


def cmd_link(args) -> None:
    from . import remote
    code = args.code or input("Pairing code (ID): ").strip()
    passphrase = args.passphrase or getpass.getpass("Passphrase (PASS): ").strip()
    try:
        url, token, room = remote.parse_pairing_code(code)
    except Exception as e:  # noqa: BLE001
        print(f"! Invalid pairing code: {e}")
        return
    cfg = {"url": url, "token": token, "room": room, "passphrase": passphrase}
    ok, msg = remote.ping(cfg)
    if not ok and not args.force:
        print(f"! Could not reach the relay: {msg}  (use --force to save anyway)")
        return
    remote.save_remote_cfg(cfg)
    print(f"Linked to room '{room}' as '{remote.local_host()}'.")
    print("Run  claude-fleet  (widget) or  claude-fleet agent  (headless) to start syncing.")


def cmd_unlink(_args) -> None:
    from . import remote
    remote.clear_remote_cfg()
    print("Unlinked — this machine is local-only again.")


def cmd_status(_args) -> None:
    from . import remote
    cfg = remote.load_remote_cfg()
    if not cfg:
        print("Remote: not configured (local-only).")
        return
    ok, msg = remote.ping(cfg)
    print(f"Machine name : {remote.local_host()}")
    print(f"Room         : {cfg.get('room')}")
    print(f"Relay URL    : {cfg.get('url')}")
    print(f"Passphrase   : {'set' if cfg.get('passphrase') else 'MISSING'}")
    print(f"Connectivity : {'OK' if ok else f'FAIL ({msg})'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="claude-fleet",
                                     description="Always-on-top LED widget for Claude Code sessions.")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("agent", help="headless cross-machine sync loop").set_defaults(func=cmd_agent)

    p = sub.add_parser("init-room", help="create a cross-machine room (needs Upstash creds)")
    p.add_argument("--url"); p.add_argument("--token"); p.add_argument("--room")
    p.add_argument("--pass", dest="passphrase"); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_room)

    p = sub.add_parser("link", help="link via a self-contained pairing code")
    p.add_argument("--code"); p.add_argument("--pass", dest="passphrase")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("set-relay", help="save Upstash relay creds (url + token)")
    p.add_argument("--url"); p.add_argument("--token")
    p.set_defaults(func=cmd_set_relay)

    p = sub.add_parser("join", help="join a room by short ID (needs relay creds set)")
    p.add_argument("--room"); p.add_argument("--pass", dest="passphrase")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_join)

    sub.add_parser("unlink", help="remove remote config").set_defaults(func=cmd_unlink)
    sub.add_parser("status", help="show remote config + connectivity").set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        cmd_widget(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
