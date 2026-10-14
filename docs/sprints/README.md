# Sprints

One file per week. Named by the Monday it starts, not by course week number —
the W3 gate date is still contested between 9 and 23 Oct, and nothing here needs
to depend on settling that first.

Each file says who is doing what, what "done" looks like, and which decisions the
week is waiting on. That is it. Long-lived contracts stay in the documents beside
this folder; these files are the short-lived layer on top.

## Why this lives in the repo

The Columbia Notion workspace is out of free blocks, so new pages and new blocks
cannot be created there. `docs/README.md` already moved the contracts here for the
same reason. Keeping the week plan in the same place means one source of truth, and
the plan gets reviewed in the same pull request as the work it describes.

## Template

```markdown
# Week of <date>

**Goal:** one sentence. What is true on Friday that is not true today.

## Seats

### <seat name> — <owner>
- [ ] Item. Done when: <observable thing>

## Decisions this week needs

| Decision | Who decides | What it blocks |
| --- | --- | --- |

## Carried over

## Dropped, and why
```

## Rules that keep this useful

**Every item names an owner.** `action-items.md` has carried "Nothing above has a
name against it" for a while, and calls that the item that makes the rest happen.
An unowned item belongs in the backlog, not in a week.

**Every item has a done condition that someone else could check.** "Work on
extraction" is not one. "Three accession numbers in the repo, one of them with a
10-K/A" is.

**Dropped items get a line saying why.** Silent disappearance is how the same
argument gets had three times.

**Two or three items per seat.** Seven people, two hats each, and a course on top.
A week that lists eight things per person is a wish, not a plan.
