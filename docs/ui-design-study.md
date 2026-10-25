# UI Design Study And Review Contract

Research date: 2026-10-14. The references below are product documentation,
official interface screenshots, and design accounts. This is a study of selected
patterns, not a claim that every product is universally preferred or that their
current apps were installed and benchmarked. Things and Bear document Apple
Design Awards; the remaining products are widely used reference products.

| Product / Primary Source | Pattern Studied | Application Here |
| --- | --- | --- |
| [Linear](https://linear.app/now/how-we-redesigned-the-linear-ui) | Aligned sidebar/header, quieter chrome, density and hierarchy stress tests | Grouped sidebar, compact header, neutral surfaces, explicit active item |
| [Notion](https://www.notion.com/help/navigate-with-the-sidebar) | Sections, daily entry points, separate workspace settings | Daily / materials / long-term / workspace navigation groups |
| [Things](https://culturedcode.com/things/) | Readable task hierarchy, primary work before secondary tools | Dashboard starts with the next action; summaries are an unframed band |
| [Craft](https://www.craft.do/blog) | Document/task organization, sync craft, progressive tooling; official blog links to Collections, Tasks and Beyond CSS articles | Long text remains the main surface; supplementary setup details expand on demand |
| [Bear](https://bear.app/) | Restrained editor, typography, advanced features revealed progressively | Neutral reading surface, no nested decorative cards, full editable drafts |
| [Todoist](https://www.todoist.com/help/todoist/get-started/get-started-with-todoist-OgNNJR) | Specific first task, sections and clear priority; deadline distinct from start date | Actionable first-use steps and task entry; do not infer deadline semantics from display |
| [Slack](https://slack.com/blog/productivity/a-redesigned-slack-built-for-focus) | Dedicated daily views, navigation distinct from the current conversation | Workspace chrome separate from chat history and composer |
| [Telegram](https://telegram.org/blog/crafting-android-design-and-more) | Few persistent bottom destinations, quick access to conversations/settings | Android Home / Chat / Tasks / More, replacing twelve scrolling peers |
| [Signal](https://signal.org/blog/a-synchronized-start-for-linked-devices/) | Explicit device linking and history ownership; pairing is separate from syncing | Device credentials separate from model key; visible connection and offline states. QR provisioning is future work, not claimed implemented |
| [Spotify](https://newsroom.spotify.com/2026-04-16/new-tablet-app-experience/) | Adapt the layout to the screen rather than scale mobile controls | Desktop sidebar, mobile drawer/native bottom navigation; persistent primary interaction |

## Design Direction

Quiet personal workspace: white reading surface, cool neutral chrome, green
command accent, amber waiting state and red error state. Fixed type sizes with
system scaling, 6-8 px radii, 44 px web / 48 dp Android controls, readable Chinese
body text. No remote fonts or external icon calls at runtime. Web icons use
vendored Lucide 0.468.0 (ISC).

## Required Review Evidence

Two independent agents simulate Apple and Meta design/front-end review
perspectives. They are not employees of those organizations. Both review the
same exact candidate revision and rendering evidence; unresolved blockers require
another iteration. Their approval does not replace user activation approval.

- Real browser interaction against the compiled core, using isolated fixtures.
- Desktop, mobile, low-height, long text, wide table and increased text-size views.
- Long chat keeps the composer accessible; async reply preserves typed text,
  focus, selection and reading position.
- Navigation has an active state, keyboard focus and accessible names.
- Connection/model steps expose actual configuration locations after login;
  loopback addresses are never called phone reachable.
- Approval shows recipients, full body/fields and actual attachment metadata.
- Android layout at phone density, system/cutout/keyboard insets, offline input
  persistence and explicit conflict recovery.

Browser simulations and Robolectric rendering are distinct from a physical
iQOO test. Actual VPN reachability, real model calls and device-specific keyboard
behavior require separate evidence; never infer those from a passing UI test.
