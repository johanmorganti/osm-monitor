# Documentation layout: CLAUDE.md for agents, decisions in their own files

**CLAUDE.md holds agent instructions only (2026-10-02).** It had grown to ~525 lines, most of it
architecture history: measurements, incidents, lessons learned. All of it was loaded into every
agent session, whatever the task, and it was hard for a human to skim. Now:

- `CLAUDE.md`: what an agent must know to work here safely: rules (each one or two lines, with a
  link to its decision), the file map, the documentation conventions.
- `docs/decisions/<slug>.md`: one architecture decision per file: what was decided, why, the
  evidence, the lessons. Indexed in `docs/decisions/README.md`. Read on demand.
- `docs/ARCHITECTURE.md`: how the system works as it stands (data flow, pages and endpoints,
  observability).
- `docs/DEPLOYMENT.md`: running and operating it.
- `TODO.md` + `docs/todo/<slug>.md`: open work (see below).

A rule that matters for every change stays in CLAUDE.md, in one or two lines; the reasoning behind
it goes to the decision file. When a decision changes, update its file and, if the rule changed,
its CLAUDE.md line in the same change.

## TODO.md stays an index, details go in `docs/todo/` (2026-09-18)

`TODO.md` grew past the point of being skimmable: each entry had accreted its full investigation
history (evidence, numbers, false starts) inline. It holds **one line per item only**: enough to
know what it is and whether it's relevant, with a link to `docs/todo/<slug>.md` for the reasoning,
evidence, and remaining steps. The same index-plus-detail-file pattern as `docs/decisions/` above,
and as the template / JS split ([template-js-separation.md](template-js-separation.md)).
