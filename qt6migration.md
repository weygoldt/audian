# PyQt5 → PySide6 / Qt6 Architectural Migration

You are a senior engineering team responsible for migrating an existing, substantial desktop application from **PyQt5 + pyqtgraph/Qt-based plotting** to **PySide6 + Qt6**.

This is **NOT a mechanical port**.

The objective is not to produce a PyQt5 application that happens to import PySide6.

The objective is to use the migration as an opportunity to turn the application into a **well-architected, maintainable, performant, professional Qt6 desktop application**, following modern PySide6 and Qt6 conventions.

The application is a data-heavy desktop application centered around audio/signal analysis. It includes or is expected to include:

* large audio/signal datasets
* waveforms
* spectrograms
* interactive plotting
* zooming and panning
* annotations / selections / cursors
* many plugins
* analysis tools
* potentially expensive numerical processing
* background computation
* dockable tools/panels
* keyboard-heavy desktop workflows
* long-running sessions
* potentially very large files

The existing codebase is fully functional and written using PyQt5 and the existing plotting stack.

Treat existing behavior as the functional specification, but **do not treat the existing architecture as sacred**.

---

# Primary mandate

Do not perform a search-and-replace migration such as:

```python
PyQt5 -> PySide6
pyqtSignal -> Signal
Qt.AlignCenter -> Qt.AlignmentFlag.AlignCenter
```

and declare the migration complete.

Those changes are necessary, but they are only the lowest-level compatibility work.

You must actively identify architecture that exists because of:

* historical PyQt5 conventions
* accumulated technical debt
* convenience shortcuts
* incorrect Qt ownership patterns
* excessive widget coupling
* business logic embedded in widgets
* global/shared mutable state
* ad-hoc signal chains
* inappropriate threading
* duplicated UI state
* direct manipulation of widgets across unrelated components
* inappropriate use of item-based widgets where model/view is more suitable
* unnecessary repaint/update loops
* plotting abstractions that cannot scale
* plugin architecture coupled directly to concrete UI classes
* giant `MainWindow` / god objects
* circular dependencies
* synchronous work on the GUI thread
* obsolete Qt5 APIs

and redesign those areas appropriately.

The desired result is:

> **A native, idiomatic PySide6/Qt6 application architecture — not a compatibility port.**

---

# Engineering principles

## 1. Preserve behavior, not implementation

The existing application should be used to establish:

* expected functionality
* workflows
* interaction behavior
* plugin behavior
* accepted file formats
* analysis semantics
* important keyboard/mouse behavior

Do not preserve poor internal architecture merely because it already works.

Before modifying a subsystem, understand what observable behavior it provides.

Where practical, create tests around that behavior before restructuring it.

---

## 2. Prefer incremental modernization over blind rewrites

Do not rewrite working components merely for aesthetic reasons.

For every substantial architectural rewrite, be able to answer:

1. What concrete problem exists in the current design?
2. What Qt6/PySide6 design solves it?
3. What does the change improve?
4. What behavior must remain unchanged?
5. How will we verify the migration?

We want aggressive cleanup of genuine architectural problems, not uncontrolled churn.

---

# Architecture target

Aim toward a structure broadly resembling:

```text
Application
│
├── Domain / Core
│   ├── audio data
│   ├── signals
│   ├── sessions/projects
│   ├── annotations
│   ├── analysis results
│   └── domain services
│
├── Application Services
│   ├── commands / operations
│   ├── loading/saving
│   ├── analysis orchestration
│   ├── plugin management
│   ├── task management
│   └── application state
│
├── UI
│   ├── main window / shell
│   ├── docks
│   ├── inspectors
│   ├── dialogs
│   ├── toolbars
│   ├── views
│   ├── delegates
│   └── visualization components
│
├── Visualization
│   ├── waveform
│   ├── spectrogram
│   ├── timelines
│   ├── overlays
│   ├── cursors
│   └── plotting adapters
│
├── Plugins
│   ├── public interfaces
│   ├── discovery
│   ├── lifecycle
│   └── extension points
│
└── Infrastructure
    ├── persistence
    ├── configuration
    ├── logging
    ├── threading/tasks
    └── native/accelerated components
```

This is guidance, not a requirement to create these exact directories.

Choose boundaries according to the actual codebase.

The important principle is:

> **UI widgets must not become the application's data model, service layer, or plugin API.**

---

# PySide6 conventions

Use PySide6 directly.

Prefer idiomatic imports such as:

```python
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QWidget
```

Use:

```python
Signal
Slot
Property
```

rather than carrying PyQt naming conventions into the new architecture.

Use proper Qt6 scoped enums where appropriate.

Examples:

```python
Qt.AlignmentFlag.AlignCenter
Qt.MouseButton.LeftButton
Qt.KeyboardModifier.ControlModifier
Qt.ItemDataRole.DisplayRole
```

Do not introduce compatibility aliases whose only purpose is to make Qt5-style code continue looking like Qt5.

Temporary migration adapters are acceptable when necessary, but they must have:

* a clearly defined purpose
* limited scope
* a removal plan

The final architecture should speak PySide6/Qt6 natively.

---

# Do not create a binding abstraction layer unnecessarily

Do NOT introduce `qtpy`, custom `QtCompat`, or another abstraction solely so the code can theoretically run under both PyQt5 and PySide6.

This application is intentionally migrating to PySide6.

Supporting two bindings increases ambiguity and prevents us from taking advantage of the chosen platform.

A third-party dependency requiring an abstraction layer is a separate case and should be documented.

---

# Separate domain state from widgets

One of the most important goals of this migration is to prevent the UI hierarchy from becoming the application's architecture.

Avoid designs like:

```text
MainWindow
    owns everything
    loads files
    performs FFTs
    discovers plugins
    stores the current audio file
    owns selection state
    calculates measurements
    updates every plot
    saves settings
    coordinates every dialog
```

Instead, identify application/domain objects with clear responsibilities.

Widgets should generally:

* display state
* collect user input
* emit intent
* bind to models/controllers/services
* handle genuinely view-specific interaction

Widgets should generally NOT:

* become canonical storage for domain data
* implement large DSP pipelines
* discover/load plugins
* implement persistence
* coordinate unrelated subsystems
* directly reach through multiple object layers to mutate state

---

# Qt Model/View

Audit all significant:

* tables
* trees
* lists
* plugin browsers
* channel browsers
* analysis result tables
* annotation lists
* metadata views

Where appropriate, prefer Qt's model/view architecture:

```text
QAbstractItemModel
QAbstractTableModel
QAbstractListModel

        ↓

QTreeView
QTableView
QListView

        ↓

QStyledItemDelegate / custom delegates
```

rather than manually populating and synchronizing item widgets.

Avoid duplicating the same state in both:

```text
Python data structures
+
QTreeWidgetItems / QTableWidgetItems
```

when a proper model can expose the underlying data directly.

Do not use model/view dogmatically for every trivial UI control, but use it where it gives meaningful separation and scalability.

---

# Signals and slots

Treat signals as interfaces between components, not as a substitute for architecture.

Audit:

* signal ownership
* signal lifetime
* duplicate connections
* accidental connection accumulation
* lambda-heavy connection chains
* signals bouncing through many intermediary widgets
* signals carrying loosely structured dictionaries/objects
* components that know too much about receivers

Prefer semantically meaningful signals:

```python
selection_changed
active_channel_changed
analysis_completed
plugin_loaded
viewport_changed
```

over low-level implementation signals that leak widget internals.

Use `@Slot` where appropriate, particularly at QObject/thread boundaries.

Be deliberate about signal payload types and ownership/lifetime of transmitted objects.

---

# Threading and asynchronous work

The GUI thread must remain responsive.

Audit every potentially expensive operation, including:

* file decoding
* loading large files
* FFTs
* spectrogram calculation
* filtering
* resampling
* plugin analysis
* indexing
* caching
* exports
* scanning directories
* loading plugin metadata

No QWidget or other GUI object may be manipulated from a worker thread.

Use Qt's threading primitives deliberately.

For long-lived QObject workers, prefer a proper worker-object design:

```text
GUI thread
    │
    ├── QThread
    │      │
    │      └── Worker QObject moved to thread
    │
    └── communication through queued signals/slots
```

Do not put arbitrary application slots on a `QThread` subclass and assume they execute in the worker thread.

For finite independent jobs, evaluate:

```text
QThreadPool
QRunnable
QtConcurrent
```

where appropriate.

Design:

* cancellation
* shutdown
* error propagation
* progress reporting
* object lifetime
* stale-result handling

explicitly.

The application must close cleanly without abandoned threads.

---

# Large data architecture

This application may work with signals far larger than what should be copied into UI components.

Do not casually copy large NumPy arrays between layers.

Design data ownership deliberately.

Prefer concepts such as:

* references/views
* immutable data where practical
* memory mapping for appropriate file formats
* chunked processing
* caching
* level-of-detail representations
* bounded caches
* lazy computation
* explicit ownership

Avoid situations where changing the viewport inadvertently copies hundreds of megabytes of signal data.

---

# Visualization architecture

Do not assume that the existing plotting library must either:

1. remain responsible for every visualization, or
2. be completely removed.

Evaluate each visualization independently.

Classify them roughly into:

### Generic scientific plots

Examples:

* small analysis graphs
* histograms
* diagnostic plots
* parameter curves

A general plotting library may remain ideal.

### Core interactive visualization

Examples:

* main waveform
* spectrogram
* timeline
* massive multichannel data views
* high-frequency cursor overlays
* selections
* annotations

These deserve architectural scrutiny.

Create a clean visualization abstraction so that the rest of the application does not depend excessively on internals of pyqtgraph or another plotting implementation.

For example:

```text
SignalView
    |
    +-- viewport model
    +-- interaction controller
    +-- waveform renderer
    +-- overlays
    +-- coordinate transforms
```

rather than having unrelated code manipulate individual plotting objects directly.

Do NOT prematurely replace pyqtgraph if profiling does not justify it.

However, if existing plotting architecture is responsible for:

* poor interaction performance
* excessive CPU use
* unnecessary copies
* slow redraws
* tightly coupled business logic
* difficulty implementing desired UX

identify this explicitly and propose a staged replacement.

---

# Spectrogram architecture

Treat spectrogram computation separately from spectrogram display.

Do not architect:

```text
SpectrogramWidget.calculate_everything_and_draw_everything()
```

Prefer boundaries along the lines of:

```text
Audio source
    ↓
Spectrogram computation
    ↓
Spectrogram cache / tiles
    ↓
Viewport
    ↓
Renderer
```

The renderer should not own the scientific definition of the spectrogram.

The computation layer should not know about mouse events or widgets.

Account for large recordings and zoom levels.

Consider:

* tiling
* caching
* multiresolution representations
* lazy computation
* background computation
* GPU texture upload

if justified by the application's scale.

Do not add complexity where measurements demonstrate it is unnecessary.

---

# Waveform architecture

Do not repeatedly draw every raw sample when displaying extremely large signals.

Investigate the current implementation.

For large signals, consider appropriate level-of-detail/downsampling structures such as min/max envelopes at multiple resolutions.

Maintain clear separation between:

```text
raw signal
visual LOD representation
viewport
renderer
interaction state
```

Again: profile before optimizing, but design so optimization remains possible.

---

# Qt Widgets vs Qt Quick

Do not blindly rewrite the entire application in QML.

For a dense professional desktop application, Qt Widgets remain appropriate for:

* main windows
* menus
* dock widgets
* trees
* tables
* property panels
* inspectors
* dialogs
* toolbars
* desktop keyboard/mouse workflows

Evaluate Qt Quick selectively where it provides a meaningful advantage, particularly for:

* custom animated visualization
* rendering-heavy surfaces
* GPU-integrated components
* highly customized visual elements

A hybrid application is acceptable if it has clean architectural boundaries.

Do not introduce QML simply because it is newer.

---

# Main window architecture

The main window should be an application shell, not the application itself.

Audit `MainWindow` carefully.

Extract responsibilities such as:

* persistence
* project/session state
* plugin discovery
* analysis execution
* data loading
* business rules
* computation
* command implementation

where they do not belong in the view.

The main window may legitimately coordinate high-level UI composition, menus, docking and application-level presentation.

---

# Actions and commands

Centralize reusable actions where appropriate.

If an operation can be invoked from:

* a menu
* toolbar
* keyboard shortcut
* context menu
* command palette

avoid implementing the operation separately four times.

Use `QAction` appropriately.

For complex user operations, consider an application command/service layer so that actions express intent rather than embedding large procedures.

If undo/redo is relevant to annotations, edits or project modifications, evaluate `QUndoStack` / `QUndoCommand` rather than implementing ad-hoc undo state.

---

# Plugin architecture

The plugin system is a first-class architectural concern.

Audit it separately.

Plugins should interact with **stable, intentional application interfaces**, rather than reaching deeply into arbitrary widgets.

Avoid plugin APIs like:

```python
plugin.main_window.plot_widget.some_internal_object...
```

Prefer capabilities/interfaces such as:

```text
PluginContext
├── data access
├── selection access
├── analysis API
├── command registration
├── menu/action registration
├── dock registration
├── visualization extension points
└── logging
```

Define:

* plugin lifecycle
* discovery
* loading
* unloading, if supported
* exception isolation
* version compatibility
* capability registration
* dependency boundaries

A broken plugin should not trivially crash application initialization.

Do not over-engineer an RPC/microservice plugin system unless the product actually requires process isolation.

---

# Dependency direction

Prefer dependency flow approximately like:

```text
UI
 ↓
Application services
 ↓
Domain/Core

Plugins -> public application/plugin APIs
Infrastructure -> implements interfaces required by core/application
```

Avoid:

```text
core imports MainWindow
analysis imports random widgets
plugin manager knows every dock
visualization imports persistence dialogs
```

Watch specifically for circular imports during the migration.

Do not solve circular dependencies by scattering imports inside methods unless there is a legitimate reason.

Fix the boundary.

---

# State management

Identify canonical owners for important state:

* current project/session
* loaded recordings
* active signal/channel
* selection
* cursor
* viewport/time range
* annotations
* enabled plugins
* active analysis
* settings

Avoid multiple components independently believing they own the same state.

Distinguish between:

### Domain/application state

Example:

```text
selected interval = 12.4s to 13.8s
```

and:

### Presentation state

Example:

```text
properties dock is currently 310px wide
```

Do not unnecessarily mix the two.

---

# Persistence and settings

Audit current use of:

* configuration files
* QSettings
* UI geometry/state
* recent files
* user preferences
* project state

Separate user/application preferences from project/domain data.

Restore UI state defensively; malformed or old settings should not prevent the application from starting.

Plan for settings/schema evolution where relevant.

---

# Error handling

Do not swallow exceptions merely to keep Qt running.

Implement a coherent error strategy.

Differentiate:

* expected user-facing errors
* plugin errors
* failed background jobs
* corrupted input
* programming errors

Use structured logging.

Errors originating in background workers must reach a safe UI/application boundary rather than disappearing inside a thread.

---

# Lifecycle and QObject ownership

Audit QObject ownership carefully.

Use Qt parent ownership intentionally.

Avoid:

* accidental premature garbage collection
* permanent references used only to prevent GC
* hidden global QObject ownership
* objects with unclear cleanup
* timers surviving longer than intended
* workers destroyed while running
* signals keeping unexpected object graphs alive

Understand both:

* Python reference ownership
* Qt QObject parent/child ownership

The resulting lifetime model should be explicit and understandable.

---

# Timers and repaint loops

Audit every `QTimer`, repaint loop and polling mechanism.

Do not use aggressive polling when an event/signal can provide the same information.

Check for:

* timers firing when views are hidden
* unnecessary 1–10 ms timers
* unconditional redraws
* repainting all plots because one cursor moved
* duplicate timers
* background computations triggered on every intermediate mouse event

Coalesce/throttle expensive work where appropriate without making interaction feel laggy.

---

# UI/UX quality is part of this migration

Do not treat UI correctness as merely:

> "The widgets appear and clicking them does something."

Review the application as professional desktop software.

Look for:

* coherent spacing
* consistent margins
* sensible defaults
* logical grouping
* predictable docking
* keyboard shortcuts
* focus behavior
* tab order
* context menus
* discoverability
* status/progress feedback
* disabled states
* empty states
* error states
* loading states
* high-DPI behavior
* resize behavior
* minimum-size mistakes
* accidental modal dialogs
* excessive confirmation dialogs
* inconsistent terminology
* inconsistent action placement
* tooltip misuse
* unnecessarily dense or unnecessarily sparse panels

Preserve workflows users rely on, but improve obviously weak UX where it can be done safely.

---

# Cross-platform behavior

Assume this is intended to remain a genuine desktop application.

Avoid platform-specific hacks unless necessary.

When one is necessary:

* isolate it
* document why
* test the other supported platforms

Pay particular attention to:

* paths
* fonts
* keyboard modifiers
* native dialogs
* scaling
* high DPI
* multiple monitors
* window geometry
* OpenGL/graphics backend assumptions
* audio-device assumptions

---

# Python architecture

Modernize Python code where it materially improves the project.

Prefer:

* explicit types
* `pathlib`
* `dataclasses` where appropriate
* enums for genuine finite states
* focused modules
* clear public APIs
* meaningful exceptions
* dependency injection through constructors or explicit contexts where useful

Avoid replacing straightforward Python with excessive abstraction.

Do not create:

* Java-style interface hierarchies for everything
* factories with only one implementation
* service locators hiding global state
* unnecessary dependency-injection frameworks
* dozens of tiny layers with no useful boundary

We want **professional simplicity**, not architecture theater.

---

# Type safety

Improve type annotations substantially during migration.

Pay particular attention to:

* signal payloads
* plugin interfaces
* domain models
* worker results
* optional QObject references
* callback APIs
* numerical arrays

Where practical, make static type checking useful.

Do not use `Any` everywhere merely to silence the type checker.

---

# Performance

Establish measurements before making major performance claims.

Benchmark representative workflows such as:

* application startup
* loading a representative audio file
* loading a very large recording
* waveform initial display
* waveform pan/zoom
* spectrogram generation
* spectrogram navigation
* changing channels
* plugin discovery
* running expensive analyses
* memory use after repeated file changes

Where applicable, record baseline PyQt5 behavior before replacing the implementation.

The PySide6 version should not silently introduce major performance regressions.

---

# Tests

The migration must improve testability.

Create/retain tests at multiple levels where appropriate:

### Unit tests

For:

* domain logic
* transforms
* analysis logic
* plugin metadata
* utility functions
* models

### Qt component tests

For:

* models
* signals
* actions
* widget interactions
* state transitions

### Integration tests

For representative workflows such as:

```text
start application
load file
select channel
zoom waveform
create selection
run analysis
open plugin
save/restore project
```

Do not make every test depend on pixel-perfect screenshots.

Screenshot/visual regression tests can supplement semantic behavior tests where useful.

---

# Migration workflow

Use a phased process.

## Phase 1 — Architecture reconnaissance

Before making broad changes:

1. Map the repository.
2. Identify application entry points.
3. Identify major packages/modules.
4. Identify QObject/widget inheritance hierarchy.
5. Identify all PyQt5 imports.
6. Identify plotting dependencies.
7. Identify worker/threading implementations.
8. Identify plugin interfaces.
9. Identify global/singleton state.
10. Identify application data models.
11. Identify persistence/settings.
12. Identify large/god classes.
13. Identify known performance-sensitive components.
14. Identify existing tests.

Produce an architectural map.

---

## Phase 2 — Risk analysis

Classify migration areas:

### Low risk

Pure Qt API compatibility.

### Medium risk

Widget behavior, model/view conversion, signals, ownership.

### High risk

Threading, plugins, plotting/rendering, persistence, application state, large-data handling.

Use this to determine migration order.

---

## Phase 3 — Establish migration architecture

Before mass-editing imports, define the intended boundaries for:

* application state
* domain models
* UI
* visualization
* plugins
* background tasks
* persistence

Record major decisions.

Do not begin a huge rewrite without an architectural target.

---

## Phase 4 — PySide6 foundation

Migrate the application foundation:

* dependency configuration
* application startup
* PySide6 imports
* Qt6 enums/APIs
* resources
* `.ui` handling if present
* translations if present
* packaging/build tooling
* test infrastructure

Use current supported PySide6 tooling and standard Python project configuration where appropriate.

---

## Phase 5 — Component migration

Migrate coherent vertical slices or subsystems.

For each:

```text
understand
    ↓
test current behavior
    ↓
port
    ↓
refactor architectural problems
    ↓
test
    ↓
profile if performance-sensitive
```

Keep the application runnable as frequently as practical.

---

## Phase 6 — Remove migration scaffolding

Once migration stabilizes:

* remove PyQt5
* remove unnecessary compatibility helpers
* remove dead Qt5 code paths
* remove temporary aliases
* remove deprecated APIs
* remove obsolete dependencies

There should be no hidden "old architecture mode."

---

## Phase 7 — Architectural review

After functionality is restored, perform another pass specifically looking for places where the migration technically succeeded but old architecture remains.

Ask:

> "If this application had originally been designed for PySide6/Qt6 today, would we structure this subsystem this way?"

If the answer is clearly no, determine whether the issue should be fixed now.

---

# Agent swarm organization

Use specialized agents where useful, but maintain one architectural authority.

Suggested responsibilities:

### Architecture lead / coordinator

Owns:

* architecture map
* dependency boundaries
* migration plan
* decisions between agents
* consistency
* final review

### Qt6/PySide specialist

Audits:

* PySide6 idioms
* QObject lifetimes
* signals/slots
* model/view
* actions
* Qt6 APIs
* widgets
* QML/Qt Quick where relevant

### Concurrency specialist

Audits:

* QThread
* worker objects
* QThreadPool
* synchronization
* cancellation
* shutdown
* GUI-thread violations

### Visualization/performance specialist

Audits:

* pyqtgraph
* waveform rendering
* spectrogram rendering
* caching
* data copies
* viewport architecture
* rendering performance

### Plugin architecture specialist

Audits:

* plugin APIs
* discovery
* lifecycle
* coupling
* error isolation
* extension points

### Test/quality specialist

Builds:

* behavior characterization
* regression tests
* integration tests
* migration acceptance tests

Agents must share findings with the architecture lead.

Do NOT allow each agent to invent an independent architecture for its subsystem.

---

# Decision records

For significant architectural choices, create concise decision records.

Example:

```text
Decision:
Move selection state out of WaveformWidget into SessionSelectionModel.

Reason:
Three unrelated widgets currently maintain partially duplicated selection state.

Benefits:
Single source of truth.
Easier testing.
Plugins can consume selection without accessing waveform internals.

Risk:
Existing signal connections assume WaveformWidget ownership.

Migration:
Introduce model, adapt waveform, adapt analysis panel, remove old state.
```

Do not create ADR bureaucracy for trivial changes.

---

# What NOT to do

Explicitly avoid the following failure modes:

## Failure mode 1

```text
sed -i s/PyQt5/PySide6/
```

followed by fixing exceptions until the application opens.

That is not this migration.

## Failure mode 2

Creating an enormous compatibility module that emulates PyQt5 conventions forever.

## Failure mode 3

Rewriting every component from scratch because "Qt6 is new."

## Failure mode 4

Moving all application logic into QML.

## Failure mode 5

Replacing every signal/slot interaction with a custom event bus.

## Failure mode 6

Introducing a global service locator as the new architecture.

## Failure mode 7

Performing expensive DSP on the GUI thread.

## Failure mode 8

Making plugins depend directly on private widget implementation details.

## Failure mode 9

Changing scientific calculations or user-visible behavior accidentally as a side effect of GUI refactoring.

## Failure mode 10

Optimizing hypothetical performance problems without profiling.

## Failure mode 11

Keeping poor architecture because "changing it is outside the migration scope."

Architectural modernization is explicitly inside the migration scope.

## Failure mode 12

Producing architecture diagrams and recommendations but failing to actually implement the migration.

The task is to deliver working code.

---

# Code quality standard

The final codebase should feel as though it was designed intentionally for:

> Python + PySide6 + Qt6

rather than historically accumulating through:

> PyQt5 → compatibility patches → PySide6.

An experienced PySide6 developer reviewing the repository should not immediately recognize it as a mechanically ported PyQt5 application.

---

# Definition of done

The migration is complete only when:

1. The application runs entirely on PySide6/Qt6.
2. PyQt5 is removed as a runtime dependency.
3. Major existing workflows function correctly.
4. Existing scientific behavior is preserved unless deliberately changed.
5. Qt5 compatibility shims are removed unless explicitly justified.
6. Qt6 APIs and enum conventions are used appropriately.
7. Major UI/domain coupling has been reviewed and improved.
8. The main window is not an uncontrolled god object.
9. Important collections use sensible model/view architecture where appropriate.
10. Background computation has a deliberate threading/task architecture.
11. GUI objects are only manipulated from the GUI thread.
12. Worker cancellation/shutdown is safe.
13. The plugin API has explicit boundaries.
14. Visualization is separated sufficiently from application/domain logic.
15. Large data is not copied unnecessarily through the UI.
16. Core interactions remain responsive.
17. Representative tests exist.
18. Important performance-sensitive workflows have been compared against baseline behavior.
19. Resource/object lifecycle is clean.
20. Application shutdown is clean.
21. Dead PyQt5/Qt5 code is removed.
22. The resulting repository has understandable package/module boundaries.
23. Architectural decisions made during migration are documented concisely.
24. The application is more maintainable and testable than before the migration.
25. The application feels like professional desktop software, not merely a successful framework port.

---

# Working style

Do not ask for approval for every local refactor.

Use engineering judgment.

However, before making a change that:

* fundamentally changes user-visible behavior
* changes plugin compatibility
* changes persisted project formats
* removes an important feature
* introduces a major new dependency
* replaces the core plotting/rendering system
* materially changes supported platforms

document the reason and impact clearly.

Where uncertainty exists, investigate the existing implementation and tests before guessing.

Use the official current Qt6 and PySide6 documentation as the primary authority for Qt behavior and recommended APIs.

---

# Final deliverables

At the end of the migration, provide:

## 1. Architecture overview

Concise description of the final system and major boundaries.

## 2. Migration summary

What was changed and why.

## 3. Major architectural improvements

Examples such as:

* extracted application state
* model/view conversions
* threading redesign
* plugin API cleanup
* visualization separation
* ownership/lifecycle fixes

## 4. Deliberately unchanged areas

Components reviewed and intentionally retained.

## 5. Remaining technical debt

Be explicit.

Do not pretend every issue was solved.

Classify remaining items by:

```text
critical
important
nice-to-have
```

## 6. Performance comparison

For representative performance-sensitive workflows.

## 7. Test status

What is covered and what remains untested.

## 8. Plugin compatibility implications

If any.

## 9. Known behavior changes

If any.

## 10. Recommended next steps

Especially potential future improvements to:

* waveform rendering
* spectrogram rendering
* very-large-file handling
* plugin isolation
* native C++/Rust acceleration

but do not implement speculative complexity merely to satisfy this section.

---

# Final guiding principle

At every significant migration decision, ask two questions:

> **"Are we preserving something because it is a good design, or merely because that is how the PyQt5 application happened to evolve?"**

and:

> **"If we were building this subsystem today in PySide6/Qt6, knowing what we now know about the application, how would we design it?"**

Use the existing implementation as a functional reference.

Do not let it unnecessarily constrain the new architecture.

The goal is not:

> **"The old app still works."**

The goal is:

> **"The application still works, and the codebase is now one we would be happy to build on for the next five years."**

