import glob
import os
import struct
import sys

import usb.backend.libusb1
import usb.core
import usb.util

VS_IF, EP_IN = 1, 0x81
SET_CUR, GET_CUR = 0x01, 0x81
PROBE, COMMIT = 0x0100, 0x0200


def log(msg):
    print(f"[capture] {msg}", file=sys.stderr, flush=True)


def write_all(data):
    view = memoryview(data)
    while view:
        view = view[os.write(1, view):]


def main():
    if os.geteuid() != 0:
        log("must run as root (kernel driver detach)")
        return 1
    be = usb.backend.libusb1.get_backend(
        find_library=lambda _: glob.glob("/opt/homebrew/lib/libusb-1.0.dylib")[0])
    dev = usb.core.find(idVendor=0x2BDF, idProduct=0x0102, backend=be)
    if dev is None:
        log("camera 2bdf:0102 not found")
        return 1
    for i in (0, 1):
        try:
            if dev.is_kernel_driver_active(i):
                dev.detach_kernel_driver(i)
        except usb.core.USBError as e:
            log(f"detach if{i}: {e}")
    usb.util.claim_interface(dev, 0)
    usb.util.claim_interface(dev, VS_IF)
    try:
        ctrl = bytearray(dev.ctrl_transfer(0xA1, GET_CUR, PROBE, VS_IF, 48))
        struct.pack_into("<HBB", ctrl, 0, 1, 2, 2)  # bmHint, bFormatIndex, bFrameIndex
        dev.ctrl_transfer(0x21, SET_CUR, PROBE, VS_IF, ctrl)
        ctrl = bytearray(dev.ctrl_transfer(0xA1, GET_CUR, PROBE, VS_IF, len(ctrl)))
        max_frame, max_payload = struct.unpack_from("<II", ctrl, 18)
        log(f"camera fmt={ctrl[2]} frame={ctrl[3]} maxframe={max_frame} maxpayload={max_payload}")
        dev.ctrl_transfer(0x21, SET_CUR, COMMIT, VS_IF, ctrl)
        while True:
            try:
                # Reading more than dwMaxPayloadTransferSize per transfer breaks header parsing.
                data = dev.read(EP_IN, max_payload, timeout=3000).tobytes()
            except usb.core.USBTimeoutError:
                continue
            if len(data) >= 2:
                # The header never sets EOF and FID never toggles, so frame boundaries are
                # recovered downstream from the pixel stream; only the payload is forwarded.
                write_all(data[data[0]:])
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except usb.core.USBError as e:
        log(f"usb error: {e}")
        return 1
    finally:
        for i in (VS_IF, 0):
            try:
                usb.util.release_interface(dev, i)
            except usb.core.USBError:
                pass
        usb.util.dispose_resources(dev)


if __name__ == "__main__":
    sys.exit(main())
