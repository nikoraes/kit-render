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

# accept the governing terms, pick "Application" then "Kit Base Editor",
# and accept the defaults for name / display name / version.
.\repo.bat template new

.\repo.bat build
```

The first build downloads the Kit kernel and the full extension stack. Expect it
to be large and to take a while; the first *launch* of a graphical app compiles
shaders for 5–8 minutes.

### The app already exists here

`apps/nikoraes.bathroom_render.kit` is committed, so you do **not** need to run
`template new` — just:

```powershell
.\repo.bat build
```

It is `kit_base_editor` with the variables substituted, plus these explicit
dependencies, because they are the ones that were missing everywhere else:

```toml
"omni.hydra.rtx" = {}              # Viewport renderer
"omni.hydra.scene_delegate" = {}   # Hydrate delegate resolution
"omni.renderer.rtx" = {}           # RTX renderer core
"omni.mdl.neuraylib" = {}          # MDL material compiler: USD->MDL->PTX
"omni.mdl" = {}                    # MDL core
```

Keeping the base editor (rather than a stripped service) means the same app
gives you a viewport for debugging materials interactively — which is what you
wanted.

## Layout

```
apps/nikoraes.bathroom_render.kit      the app manifest (renderer + MDL + viewport)
source/nikoraes.bathroom_render/
    render_stage.py                    headless render: open stage, warm up, capture PNG
    headless_renderer.kit              minimal service config (no viewport)
_build/windows-x86_64/release/kit/kit.exe    the real launcher, after build
```

## Running a render

```powershell
set OMNI_KIT_ACCEPT_EULA=1

.\_build\windows-x86_64\release\kit\kit.exe `
  .\_build\windows-x86_64\release\apps\nikoraes.bathroom_render.kit `
  --exec source\nikoraes.bathroom_render\render_stage.py -- `
  --scene <scene.usda> --png <out.png> --warmup 240
```

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