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

# ---------------------------------------------------------------------------
# Configuration comes from the ENVIRONMENT, not argv.
#
# Measured, not assumed: Kit passes NO extra arguments to a --exec script. Both
# of these give the script argv == [script_path] and nothing else, because Kit
# consumes everything after the script name as its own settings:
#
#     --exec render_stage.py -- --scene S --png P     -> argv = [script]
#     --exec render_stage.py --scene S --png P        -> argv = [script]
#
# That is what killed the first Windows run: --scene/--png never arrived,
# argparse called sys.exit(2), and the SystemExit took the whole app down --
# which is why the UI stalled at 7.6% RTX loading and asked to save the stage.
#
# So read the same RENDERQ_* variables the render queue already uses. argv is
# still honoured when present, for running the script outside Kit.
# ---------------------------------------------------------------------------
_ENV = {
    "scene": "RENDERQ_SCENE",
    "png": "RENDERQ_PNG",
    "spp": "RENDERQ_SPP",
    "rendermode": "RENDERQ_RENDERMODE",
    "warmup": "RENDERQ_WARMUP",
    "mdl_root": "RENDERQ_MDL_ROOT",
    "capture": "RENDERQ_CAPTURE",
}


def _cfg(name, default=None, cast=str):
    """Environment first, then --flag on argv, then the default."""
    v = os.environ.get(_ENV[name])
    if v in (None, ""):
        v = _ARGV.get(name.replace("_", "-"))
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except (TypeError, ValueError) as e:
        raise SystemExit("[kit] bad value for %s (%s): %r"
                         % (name, _ENV[name], v)) from e


# Tolerant parse: never let argparse exit, because that kills the whole app.
_p = argparse.ArgumentParser(add_help=False)
for _f in ("--scene", "--png", "--spp", "--rendermode", "--warmup",
           "--mdl-root", "--capture"):
    _p.add_argument(_f)
_ARGV, _unknown = _p.parse_known_args()
_ARGV = {k: v for k, v in vars(_ARGV).items() if v is not None}

_args = argparse.Namespace(
    scene=_cfg("scene"),
    png=_cfg("png"),
    spp=_cfg("spp", 512, int),
    rendermode=_cfg("rendermode", "PathTracing"),
    warmup=_cfg("warmup", 240, int),
    mdl_root=_cfg("mdl_root"),
    capture=_cfg("capture", "auto"),
)

# A module-level `raise SystemExit` here would be logged as an error and would
# kill the app mid-initialisation; post_quit is the clean way out.
_CONFIG_OK = bool(_args.scene and _args.png)
if not _CONFIG_OK:
    print("[kit] MISSING CONFIG: set %s and %s (Kit does not forward argv to "
          "--exec scripts)" % (_ENV["scene"], _ENV["png"]), flush=True)

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


if _CONFIG_OK:
    asyncio.ensure_future(_render())
else:
    import omni.kit.app as _ka
    _ka.get_app().post_quit(2)
