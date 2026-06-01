TASK: Improve Trading Terminal Charting Experience

We already have the chart integrated and working. I now want to improve the overall charting UX to be closer to TradingView's workflow and productivity.

Objective

Review the existing chart implementation and:

Fix any non-working or partially working drawing tools.
Improve drawing tool management.
Add TradingView-style shortcuts.
Improve usability for active traders.
Add favorites system for drawing tools.
Ensure the architecture remains scalable and modular.
FEATURE 1: FAVORITE DRAWING TOOLS

Implement a Favorites section at the top of the drawing toolbar.

Requirements:

User can click ⭐ on any drawing tool.
Favorited tools appear in a dedicated Favorites toolbar.
Favorites persist across sessions.
Store in localStorage initially.
Future-ready for database sync.

Example:

Favorites
─────────────
Trend Line
Horizontal Line
Ray
Rectangle
Long Position
Short Position
Fibonacci Retracement
Text
Arrow

Provide:

Add Favorite
Remove Favorite
Reorder Favorites (drag & drop)
FEATURE 2: DRAWING TOOL AUDIT

Review all currently implemented tools.

Verify:

Selection works
Creation works
Dragging works
Resizing works
Deletion works
Undo support works
Redo support works

Fix all issues found.

FEATURE 3: TRADINGVIEW-STYLE SHORTCUTS

Implement global keyboard shortcuts.

Drawing Tools:

Shortcut	Action
Alt + T	Trend Line
Alt + H	Horizontal Line
Alt + R	Ray
Alt + F	Fibonacci Retracement
Alt + C	Rectangle
Alt + A	Arrow
Alt + X	Text Tool
Alt + L	Long Position
Alt + S	Short Position

Chart Actions:

Shortcut	Action
Delete	Delete Selected Drawing
Ctrl + Z	Undo
Ctrl + Shift + Z	Redo
Ctrl + C	Copy Drawing
Ctrl + V	Paste Drawing
Esc	Cancel Current Tool
Space	Crosshair Tool
H	Reset Chart View

Navigation:

Shortcut	Action
1	1 Minute
5	5 Minute
15	15 Minute
30	30 Minute
60	1 Hour
D	Daily
W	Weekly
FEATURE 4: DRAWING OBJECT MANAGER

Add a panel similar to TradingView Object Tree.

Show:

Drawings
-----------------
Trend Line #1
Trend Line #2
Rectangle #1
Fib #1
Text Note #1

Actions:

Select
Rename
Hide
Show
Lock
Unlock
Delete
FEATURE 5: LOCK DRAWINGS

Add:

Lock Drawing
Unlock Drawing

Locked drawings:

Cannot move
Cannot resize
Cannot delete accidentally

Visual indicator:
🔒 icon

FEATURE 6: DRAWING TEMPLATES

Allow saving style templates.

Example:

My Trendline Style
Color: Blue
Width: 2
Dashed: False

User can:

Save Template
Apply Template
Delete Template

Persist in localStorage.

FEATURE 7: MULTI-SELECT

Implement:

Shift + Click
Box Selection

Actions on multiple drawings:

Move
Delete
Lock
Change Style
FEATURE 8: CONTEXT MENU

Right-click on drawing:

Edit
Duplicate
Lock
Bring To Front
Send To Back
Delete
Add To Favorites
FEATURE 9: DRAWING SEARCH

Inside object manager:

Search drawings...

Filter by:

Type
Name
Created Date
FEATURE 10: LONG / SHORT POSITION TOOL ENHANCEMENT

Enhance position tools to display:

Entry
Stop Loss
Target
Risk Amount
Reward Amount
Risk Reward Ratio
Position Size

Allow drag-to-adjust:

Entry
SL
Target

Realtime calculations update automatically.

FEATURE 11: USER PREFERENCES

Persist:

Favorite tools
Last selected tool
Toolbar position
Drawing templates
Shortcut preferences

Store via:

localStorage

with clean abstraction layer for future backend sync.

CODE QUALITY REQUIREMENTS
Modular architecture
TypeScript types for all drawing entities
Reusable hooks
No duplicated logic
Proper event cleanup
Optimized rendering
Avoid memory leaks
Keep architecture ready for future multi-chart layouts
DELIVERABLES

Provide:

Full implementation plan.
Architecture changes required.
New components to create.
Existing components to modify.
Data models/interfaces.
Keyboard shortcut manager implementation.
Favorites system implementation.
Object manager implementation.
Any performance concerns and optimizations.

Do not provide only high-level suggestions. Review the current codebase and implement the changes.