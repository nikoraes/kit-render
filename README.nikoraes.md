# Bathroom Render — Omniverse Kit app

A Kit app built from NVIDIA's
[kit-app-template](https://github.com/NVIDIA-Omniverse/kit-app-template),
used to render the bathroom USD with the real MDL→PTX material pipeline.

**Why this exists:** the standalone `ovrtx` wheel (`0.5.0.377615`) renders every
material carrying a `UsdUVTexture` as a saturated red fallback. Thirteen
authorings were tested and measured on the actual scene — UV coordinates, node
hierarchy and placement, light colour, colour space, HDRI tint, channel order,
asset presence, PNG format, three `UsdUVTexture` forms, three
`UsdMdlxMaterial` binding forms, three texture-streaming modes — and only
removing the texture nodes renders correctly. Materials and lighting are sound;
the stripped wheel is not.

The `omniverse-kit` PyPI wheel is a **bare kernel** (10 Python modules, empty
`omni/extensions/`, no `omni.usd` / `omni.hydra.rtx` / `omni.mdl.neuraylib`).
The extensions live in NVIDIA's extension registry, which the template's tooling
pulls at build time. So the template is the route, not pip.

## Setup on Windows

```powershell
git clone https://github.com/nikoraes/kit-render.git
cd kit-render

.\tools\seed-packman.cmd
.\repo.bat build
```

`.cmd` rather than `.ps1`, deliberately. This machine blocks PowerShell scripts
(`PSSecurityException`) *and* its PowerShell is missing cmdlets the script needed
(`Get-FileHash is not recognized`). Rather than work around both, the logic lives
in `tools/seed_packman.py` and the `.cmd` is a one-line launcher — Python is
already present and working here, and there is one implementation instead of a
shell dialect lottery.

Behind a corporate TLS-inspecting proxy, Zscaler blocks `.7z` from CDN hosts, and
packman needs nine of them. The seed pre-places them in `C:\packman-repo\chk\`
so packman treats them as already installed and never requests them. See
[docs/zscaler-tls.md](docs/zscaler-tls.md) for the CA bundle
(`C:\Users\NRaes\.ca-bundle.pem`) that fixes the certificate error first.

The extension stack needs no seed. Kit's registry client fetches it
independently of packman, and `repo.toml` sets `kit_use_urllib_for_http = true`
so that fetch goes over Kit's Python urllib — the stack that reads the CA bundle.
The native client library has no CA-bundle environment variable at all, which is
why the build failed with *"One or a few of extensions failed to pull"* before
that switch was set.

Expect the first build to pull ~7.7 GB of extensions into
`%LOCALAPPDATA%\ov\data\exts\v2\`, and the first *graphical* launch to
compile shaders for 5–8 minutes.

### The app already exists here

`source/apps/nikoraes.bathroom_render.kit` is committed, so there is no
`repo template new` step. It is `kit_base_editor` with the variables
substituted, plus four dependencies named explicitly because they are the ones
a textured render needs:

```toml
"omni.hydra.rtx" = {}              # Viewport renderer
"omni.hydra.scene_delegate" = {}   # Hydrate delegate resolution
"omni.mdl.neuraylib" = {}          # MDL material compiler: USD->MDL->PTX
"omni.mdl" = {}                    # MDL core
```

Keeping the base editor (rather than a stripped service) means the same app
gives you a viewport for debugging materials interactively.

> **Do not add dependencies from memory.** A hand-written `omni.renderer.rtx`
> made the solver fail with *"(none found)"* — that extension does not exist.
> Check a real name against the registry index the client caches at
> `%LOCALAPPDATA%\ov\data\exts\v2\index\*\summaries.json` (670 packages
> across `kit/prod/default` and `kit/prod/sdk`).

Verified on a clean checkout: `repo.sh build` exits 0, the solver resolves all 53
declared extensions, and the resolved version lock is written into the staged
app. The app is read from `source/apps/` because staging runs *after*
precaching — pointing `[repo_precache_exts] apps` at `_build/` appears to work
on an incremental build (the stale staged copy is already there) and then fails
on a clean one.

## Layout

```
apps/nikoraes.bathroom_render.kit      the app manifest (renderer + MDL + viewport)
source/nikoraes.bathroom_render/
    render_stage.py                    headless render: open stage, warm up, capture PNG
    headless_renderer.kit              minimal service config (no viewport)
_build/windows-x86_64/release/kit/kit.exe    the real launcher, after build
```

## Running a render

Configuration is passed through **environment variables**, not command-line
arguments. Kit hands a `--exec` script no arguments at all — measured, not
assumed; both of these give the script `argv == [script_path]`:

```
--exec render_stage.py -- --scene S --png P     -> argv = [script]
--exec render_stage.py --scene S --png P        -> argv = [script]
```

Kit consumes everything after the script name as its own settings. The first
Windows run failed exactly this way: argparse called `sys.exit(2)`, the
`SystemExit` took the whole app down, and the UI stalled at 7.6% RTX loading and
asked to save the stage.

```powershell
$env:OMNI_KIT_ACCEPT_EULA = "1"
$env:RENDERQ_SCENE  = "C:\path\to\scene.usda"
$env:RENDERQ_PNG    = "C:\path\to\out.png"
$env:RENDERQ_WARMUP = "240"

.\_build\windows-x86_64\release\kit\kit.exe `
  .\_build\windows-x86_64\release\apps\nikoraes.bathroom_render.kit `
  --exec source\nikoraes.bathroom_render\render_stage.py
```

| variable | default | meaning |
|---|---|---|
| `RENDERQ_SCENE` | — | USD stage to open (required) |
| `RENDERQ_PNG` | — | output PNG (required) |
| `RENDERQ_WARMUP` | 240 | frames to step while MDL/PTX compiles |
| `RENDERQ_SPP` | 512 | samples per pixel |
| `RENDERQ_RENDERMODE` | PathTracing | or RayTraced |
| `RENDERQ_MDL_ROOT` | — | dir holding `mdl/Base/OmniSurface.mdl` |
| `RENDERQ_CAPTURE` | auto | `viewport`, `framebuffer`, or `auto` |

`--scene`/`--png` still work when running the script outside Kit, where argv is
normal. `tools/diagnostics/kit_argv_probe.py` re-measures the argv behaviour if
this ever needs rechecking.

For the interactive viewer instead — same app, no `--exec`:

```powershell
.\_build\windows-x86_64\release\kit\kit.exe `
  .\_build\windows-x86_64\release\apps\nikoraes.bathroom_render.kit
```

## Two details that are load-bearing

**`OMNI_KIT_ACCEPT_EULA=1` is required.** Without it Kit prompts on stdin, which
hangs the render queue.

**The warm-up is not optional.** MDL/PTX compilation is asynchronous; capturing
before it finishes yields uninitialised surfaces. `render_stage.py` awaits
`usd_context.next_frame_async()` rather than busy-looping, because Kit drives its
own plugins from the event loop the script is standing on.

If a `UsdMdlxMaterial` renders untextured, the MDL search path is unset — pass
`--mdl-root <dir holding mdl/Base/OmniSurface.mdl>`.

## renderq integration

The queue treats this as an installed Kit app: it runs `kit.exe` from
`_build/windows-x86_64/release/kit/` with the app manifest, rather than bundling
a renderer. That is deliberate — the whole point is the extension stack, which
only this build produces.