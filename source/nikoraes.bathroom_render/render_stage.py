#!/usr/bin/env python3
r"""Headless Kit render: open a USD stage, let MDL/PTX compile, capture a PNG.

Verified against omni/scripts/test_og_rtx_save_to_disk.py in the
omniverse-kit 110.3.0.371399 wheel, which is where the async pattern below
comes from:

  * Kit's `--exec` IMPORTS this file, so module-level code runs. Do not guard
    with `if __name__ == "__main__"`.
  * `asyncio.ensure_future(coro)` starts the work. `omni.kit.async_core` and
    `run_coroutine_util` do NOT exist in this wheel.
  * `await usd_context.next_frame_async()` yields to Kit's loop so texture and
    MDL compilation can advance. This is load-bearing: capturing before
    compilation finishes yields uninitialised surfaces.
  * `omni.kit.app.get_app().post_quit(code)` exits.

Launch (there is no kit.exe -- the wheel declares no entry points, and
omni/kit_app.py bootstraps carb + CARB_APP_PATH + PYTHONPATH itself):

    set OMNI_KIT_ACCEPT_EULA=1
    .venv-kit\Scripts\python.exe -m omni.kit_app headless_renderer.kit \
        --exec render_stage.py -- --scene <s.usda> --png <out.png>

OMNI_KIT_ACCEPT_EULA=1 is required: omni.kit_app.check_eula() otherwise calls
input() and exits, which would hang a worker with no stdin.

MDL assets: point --mdl-root at the directory holding @mdl/Base/OmniSurface.mdl@,
or RENDERQ_MDL_ROOT. The search path is set BEFORE the stage opens, otherwise a
UsdMdlxMaterial composes empty and renders untextured.
"""
import argparse
import asyncio
import os
import sys

import carb
import omni.kit.app
import omni.usd

_parser = argparse.ArgumentParser()
_parser.add_argument("--scene", required=True)
_parser.add_argument("--png", required=True)
_parser.add_argument("--spp", type=int, default=512)
_parser.add_argument("--rendermode", default="PathTracing")
_parser.add_argument("--warmup", type=int, default=240)
_parser.add_argument("--mdl-root", default=None)
_parser.add_argument("--capture", default="auto", choices=("auto", "viewport",
                                                          "framebuffer"))
_args = _parser.parse_args()

MDL_ROOT = _args.mdl_root or os.environ.get("RENDERQ_MDL_ROOT", "")


def _log(msg):
    print(f"[kit] {msg}", flush=True)


async def _wait_frames(n):
    """Yield n times to Kit's loop. Never busy-loop app.update() -- Kit drives
    its own plugins from the loop we are standing on."""
    ctx = omni.usd.get_context()
    vp = None
    try:
        from omni.kit.viewport.utility import get_active_viewport
        vp = get_active_viewport()
    except Exception as e:                       # noqa: BLE001
        _log(f"no viewport utility ({type(e).__name__}); yielding to app instead")
    for _ in range(n):
        if vp is not None:
            await ctx.next_frame_async(vp)
        else:
            await omni.kit.app.get_app().next_update_async()
        await asyncio.sleep(0.01)


def _capture(out, mode):
    """Write the PNG. Viewport first; the RTX framebuffer is the headless path.
    Returns True on success."""
    if mode in ("auto", "viewport"):
        try:
            from omni.kit.capture.viewport import capture_viewport_to_file
            from omni.kit.viewport.utility import get_active_viewport
            vp = get_active_viewport()
            if vp is not None:
                capture_viewport_to_file(viewport_api=vp, file_path=out)
                if os.path.isfile(out):
                    _log(f"captured via viewport -> {out}")
                    return True
            _log("no active viewport (headless); falling back to framebuffer")
        except Exception as e:                   # noqa: BLE001
            _log(f"viewport capture unavailable: {type(e).__name__}: {e}")

    s = carb.settings.get_settings()
    s.set("/rtx/capture/framebuffer/enable", True)
    s.set("/rtx/capture/framebuffer/clear", True)
    s.set("/rtx/capture/framebuffer/count", 1)
    s.set("/rtx/capture/framebuffer/filepath", out)
    _log(f"framebuffer capture armed -> {out}")
    return False


async def _render():
    rc = 1
    try:
        if MDL_ROOT:
            if os.path.isdir(MDL_ROOT):
                carb.settings.get_settings().set("/rtx/mdl/searchPath", MDL_ROOT)
                _log(f"MDL search path: {MDL_ROOT}")
            else:
                _log(f"MDL root does not exist: {MDL_ROOT}")

        s = carb.settings.get_settings()
        s.set("/rtx/rendermode", _args.rendermode)
        s.set("/rtx/pathtracing/spp", _args.spp)
        iso = os.environ.get("RENDERQ_ISO")
        if iso:
            s.set("/rtx/camera/exposure", float(iso))
            _log(f"exposure from RENDERQ_ISO={iso}")

        out = os.path.abspath(_args.png)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)

        ctx = omni.usd.get_context()

        def on_open(result, err):
            _log(f"open_stage result={result} err={err}")
            if not result:
                carb.log_error(f"stage open failed: {err}")
                omni.kit.app.get_app().post_quit(-1)
                return
            asyncio.ensure_future(_after_open(out))

        rc = 0
        ctx.open_stage_with_callback(os.path.abspath(_args.scene), on_open)
    except Exception as e:                       # noqa: BLE001
        _log(f"EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    omni.kit.app.get_app().post_quit(rc)


async def _after_open(out):
    stage = omni.usd.get_context().get_stage()
    try:
        _log(f"stage has {sum(1 for _ in stage.Traverse())} prims")
    except Exception:                            # noqa: BLE001
        _log("stage opened (prim count unavailable)")

    _log(f"warming up {_args.warmup} frames for MDL/PTX compile ...")
    await _wait_frames(_args.warmup)
    _log("warm-up done; settling 10 frames before capture")
    await _wait_frames(10)

    if _capture(out, _args.capture):
        pass
    else:
        # framebuffer is async: give it frames, then confirm the file exists
        for _ in range(30):
            await _wait_frames(1)
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                break

    if os.path.isfile(out):
        _log(f"OK {out} ({os.path.getsize(out)} bytes)")
        omni.kit.app.get_app().post_quit(0)
    else:
        _log(f"NO OUTPUT at {out}")
        omni.kit.app.get_app().post_quit(1)


asyncio.ensure_future(_render())
