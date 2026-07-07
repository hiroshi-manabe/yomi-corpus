# Yomi Corpus Review UI Workflow Notes

## 1. Use a movement model, not a static list model

Documents should move through workflow states.

A document can go directly from final review to resolved:

```text
Final Review Queue
    ↓
Resolved
```

Or, if stronger correction is needed, it can move through strong repair:

```text
Final Review Queue
    ↓
Strong Repair Queue
    ↓
Resolved
```

The top-level workflow states should probably be:

```text
Pending Final Review
Pending Strong Repair
Resolved
```

“Final review completed, no strong repair needed” and “Strong repair completed” should both be merged into **Resolved**, because operationally they both mean:

> This document has no remaining work in this pack.

However, the path can still be kept as metadata:

```text
1 — Resolved via final review
4 — Resolved after strong repair
```

## 2. Active queues should contain only actionable documents

If a document has finished final review, it should leave the Final Review Queue.

Example initial state:

```text
Final Review Queue:
1 2 3 4 5 6 7 8 9 10

Strong Repair Queue:
empty

Resolved:
empty
```

After reviewing documents 1–5, with 4 and 5 needing strong repair:

```text
Final Review Queue:
6 7 8 9 10

Strong Repair Queue:
4 5

Resolved:
1 2 3
```

This keeps the meaning of “queue” clean:

> A queue shows things that can currently be worked on there.

## 3. Resolved should be unified and sorted by document number

Resolved should not be grouped primarily by route. It should probably be shown in original document order:

```text
Resolved:
1 2 3
```

or:

```text
1 — final review only
2 — final review only
3 — final review only
```

The reason is that Resolved is mainly for reassurance and traceability:

> These documents are done; they did not disappear.

Sorting by document number lets the user understand pack progress naturally.

## 4. Add a Pack Map as a visual overview

Because the movement model becomes hard to read as vertical lists, a **Pack Map** is useful.

Example:

```text
Pack Map

[1✓] [2✓] [3✓] [4!] [5!] [6F] [7F] [8F] [9F] [10F]
```

Where:

```text
✓ = resolved
! = strong repair pending
F = final review pending
```

The Pack Map answers:

> Where is each document in the whole pack?

It should show all documents, regardless of current state.

## 5. Keep queues as the primary action surface

The Pack Map should mostly be for overview and light navigation. The actual work selection should happen in the queues.

Recommended role split:

```text
Pack Map
= status overview / document navigation

Queues
= select work range / start task
```

Clicking a tile in the Pack Map might open details or focus the corresponding queue item, but batch selection and task launching should happen in the queue area.

## 6. Avoid rectangular selection in 2D grids

A 2D tile layout is good for seeing state, but bad for selecting ordered ranges.

For example, “6 through 10” is a linear range. Once tiles wrap into rows, rectangular selection can accidentally select strange ranges.

Avoid this:

```text
drag rectangle across tiles
```

Prefer this:

```text
From [6 ▼] to [10 ▼]
[Start Final Review]
```

## 7. Range selection can be combo-box based

Combo boxes are not a compromise here. They may actually be the cleanest solution.

Example:

```text
Final Review Queue
[6] [7] [8] [9] [10]

From [6 ▼] to [10 ▼]
Selected: 5 documents
[Start Final Review]
```

The dropdowns should list only documents currently available in that queue.

For Strong Repair:

```text
Strong Repair Queue
[4] [5]

From [4 ▼] to [5 ▼]
Selected: 2 documents
[Start Strong Repair]
```

You could also add quick actions such as:

```text
[Take next 5] [Select all] [Clear]
```

For normal operation, “Take next N” may be more convenient than manually choosing From/To.

## 8. Queue tiles can also be 2D, but only visually

It is fine for queues to show document tiles in a wrapped 2D layout:

```text
Final Review Queue

[11] [12] [13] [14] [15] [16] [17] [18]
[19] [20] [21] [22] [23] [24]
```

But the wrapping should be purely visual. Semantically, the queue is still linear and ordered by document number.

So:

```text
Tiles = preview of available documents
Combo boxes = actual range selection
Button = start task
```

## Overall proposed UI structure

A possible structure:

```text
Unified Yomi Review

Pack Map
[1✓] [2✓] [3✓] [4!] [5!] [6F] [7F] [8F] [9F] [10F]

Work Queues

Final Review
Available: 5 documents
[6] [7] [8] [9] [10]
From [6 ▼] to [10 ▼]
[Start Final Review]

Strong Repair
Available: 2 documents
[4] [5]
From [4 ▼] to [5 ▼]
[Start Strong Repair]

Resolved
3 documents
[1] [2] [3]
```

## Core design principle

> **Pack Map shows the whole pack. Queues show what can be worked on now. Resolved shows what is already done. Range selection stays linear, even if the display is tile-based.**
