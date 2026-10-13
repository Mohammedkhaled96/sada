# Accessibility requirements

The intended users are blind and use JAWS or NVDA. This is a constraint on the
design, not a feature to add later — several decisions in the original plan are
actively wrong for a screen reader user and are corrected here.

## What changed from the first plan

| Original plan | Problem | What we do instead |
|---|---|---|
| Show progress in Word's status bar | `StatusBar` is not reliably announced by JAWS or NVDA, and polling it does not raise an event | A modeless progress dialog whose accessible name updates, plus an explicit announcement at each milestone |
| Insert the transcript progressively, paragraph by paragraph | Moving the insertion point while the user is reading yanks the screen reader cursor away mid-sentence | Insert once, at a stable point. Live insertion becomes opt-in and is off by default |
| Tray icon shows state and cancels jobs | A tray icon is unreachable without a mouse and announces nothing useful | Keyboard path for both: a shortcut opens a status dialog that can cancel |
| Error codes and dialog boxes | Codes mean nothing read aloud | Full Arabic sentences describing what happened and what to do |

## Rules for the Word side

**Keyboard only, start to finish.** Shortcut → file picker → progress → result,
with no step that needs a mouse and no unlabeled control. Use Word's native
`Application.FileDialog`, which JAWS and NVDA already handle well, rather than a
custom picker that would need its own accessibility work.

**Speak every state change.** At minimum: the file was accepted, the language
that was detected, periodic progress, completion with the word count, and any
error. Silence for ten minutes during a long file is indistinguishable from a
hang.

**Progress must not be chatty.** Announcing every segment would make the
transcript unreadable while it runs. Announce on a time interval, or on
percentage milestones, and let the user query status on demand with a shortcut.

**Put the cursor at the start of the inserted text** when the job finishes, so
the user can begin reading immediately with their normal reading keys, and make
the insertion a single undo record so one Ctrl+Z reverses all of it.

**Right-to-left matters for reading order, not just looks.** Set
`ParagraphFormat.ReadingOrder = wdReadingOrderRtl` and the paragraph alignment
for Arabic output, because a screen reader navigating by word follows the
paragraph's reading order.

**Never steal focus.** A dialog that appears mid-typing and grabs focus loses
the user their place. The progress dialog opens where the user invoked it and
does not re-focus itself on updates.

## Rules for the daemon side

The daemon has no user interface, so its obligation is to make an accessible
client possible:

- `GET /jobs/<id>` returns everything a spoken status needs in one call —
  `state`, `percent`, `language`, `eta_seconds`, `words` — so the client never
  has to compose a sentence from several requests.
- `?format=text` returns `KEY=VALUE` lines, which VBA can parse with `Split`,
  so the client does not need a JSON parser to build an announcement.
- Errors come back as sentences in the `message` field, ready to be spoken,
  rather than as status codes alone.
- The tray icon must never be the only way to see state or cancel; the HTTP API
  is the real interface and the icon is a convenience.

## Testing

Automated tests cannot confirm this. It needs a pass with NVDA actually running:
tab through every control, confirm each announces a useful name and role, run a
long file and confirm progress is audible without being overwhelming, and
confirm cancel is reachable and confirmed aloud.

Open question for the user: whether they use a screen reader themselves, which
decides whether we test together or need to recruit a tester.
