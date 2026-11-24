# v0.1.0 acceptance

The browser run used Hermes WebUI v0.52.732 (`ff60081cadd9587a6e7e48a1e9daf898b502a88a`) on a separate loopback instance. The extension was installed from the Python release archive into a separate WebUI state directory. The live profile, delegation and Kanban data in the browser came from isolated SQLite fixtures with the native schemas. DEMO screens use synthetic data.

| Screen | Browser capture |
| --- | --- |
| Live Agent Graph | [Topology, inspector and recent activity](../artifacts/screenshots/01-graph.png) |
| Team Monitor | [Agent status and search](../artifacts/screenshots/02-team.png) |
| Communications | [Event timeline](../artifacts/screenshots/03-communications.png) |
| Mission Control | [Native status columns and project progress](../artifacts/screenshots/04-mission-control.png) |
| Usage Analytics | [Usage summaries and chart](../artifacts/screenshots/05-usage.png) |
| Infrastructure | [Four demo GPUs and engine metrics](../artifacts/screenshots/06-infrastructure.png) |
| Live fixtures | [Authenticated proxy and observed records](../artifacts/screenshots/07-live-fixture.png) |
| Light theme and Russian | [Russian graph view](../artifacts/screenshots/08-light-ru.png) |

The browser run exercised filters, node details, zoom, keyboard panning, fullscreen, a new delegation arriving through polling, an unavailable collector, Kanban card inspection, chart hover, and returning to the host WebUI. Twelve backend tests and two installer option tests passed. On the isolated WebUI, install, update, disable, enable, uninstall and rollback were checked through page reloads. Separate installer tests retained neighboring extension entries. Managed Linux service startup and rollback were exercised with systemd test doubles. The private inference nodes were inspected using their existing read-only interfaces. The managed service on the target Linux host remains to be accepted.

Hermes usage records contain cumulative token counters per session or model. The activity chart groups each available cumulative record by its last timestamp. Cache and reasoning counters are shown when present. Attribution, per-request timings and retry data remain unavailable wherever the sources lack those fields. Engine TTFT comes from differences in the native vLLM histogram between successful scrapes.

Communications exposes event metadata, tool names when recorded, and links to supported native sessions. Task bodies, model prompts, tool arguments and tool output are omitted. Visible task titles use pattern-based credential filtering. Native Kanban cards can include verified session identifiers, dependencies and attempt metadata. A Pi job or PR link requires an explicit source that provides the relationship.

Visual research used the [Agent Teams AI gallery](https://agentteams.live/#screenshots). HCC uses original UI code and a robot atlas made with ImageGen. The [WebUI extension contract](https://github.com/nesquena/hermes-webui/blob/master/docs/EXTENSIONS.md) governs registration and proxy consent.

The bundled [avatar atlas](../web/avatars.png) was generated using built-in ImageGen with this prompt.

> Create one square texture atlas of original premium 3D robot avatar portraits for a dark operations console. EXACT 2x2 even grid with no grid borders, each cell square, each android centered exactly at the cell center, medium close-up of head and shoulders, generous margin. Uniform deep near-black navy backdrop #080f1c with no typography. Top left cobalt-blue industrial astronaut lead robot with bright visor, top right violet agile field robot with ear-like antennae, bottom left green round survey bot face, bottom right amber angular engineer bot. All four share sleek machined tiny mechanical details, richly colored reflective anodized panels, black glass, vivid luminous eyes, rim lighting, polished game art. The image must read as four self-contained circular-croppable avatar portraits. No lettering, no panel outlines, no symbols, no watermarks.
