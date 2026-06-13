You are a Senior Frontend Architect building a professional charting platform.

Current platform already has:

Candlestick chart
Drawing tools
Indicators
Layouts
Replay
Watchlist
Broker Integration

Goal:

Make all drawing tools behave as close as possible to TradingView Desktop/Web.

This is NOT a visual redesign.

This is a functionality and UX replication project.

GLOBAL DRAWING TOOL WORKFLOW

Every drawing tool must follow this workflow:

Creation
Select Tool
→ Cursor Changes
→ User Places Points
→ Drawing Created
→ Drawing Selected Automatically
→ Floating Toolbar Appears
Selection

Single click:

Select Object
Show Anchor Points
Show Floating Toolbar

Double click:

Open Full Settings Modal

Right click:

Context Menu
Dragging

Support:

Move Entire Object
Move Individual Anchors
Snap To Price
Snap To Candle
Object States
Idle
Hover
Selected
Locked
Hidden

Visual feedback required.

FLOATING TOOLBAR

When object selected show TradingView-style toolbar.

Actions:

Color
Line Style
Line Width
Text
Lock
Hide
Clone
Copy
Delete
Bring Forward
Send Backward
Object Tree
Settings
SETTINGS PANEL

All tools must support:

Style Tab
Color
Opacity
Border Color
Border Width
Line Style
Extend Left
Extend Right
Background Fill
Show Price Labels
Show Time Labels
Coordinates Tab
Price Values
Time Values
Anchor Coordinates
Visibility Tab
1m
3m
5m
15m
1h
4h
1D
1W
1M

Per timeframe visibility.

TOOL SPECIFICATIONS
Trend Line

Workflow:

Select Tool
Click Start
Click End
Finish

Settings:

Color
Width
Style
Extend Left
Extend Right
Ray
Arrow
Price Label
Length
Angle

Shortcuts:

Alt + Drag = Clone
Delete = Remove
Ctrl + C = Copy
Ctrl + V = Paste
Arrowed Line

Settings:

Arrow Type
Arrow Size
Line Width
Line Color
Horizontal Line

Settings:

Price Label
Line Color
Line Width
Line Style
Alert Creation
Vertical Line

Settings:

Date Label
Line Color
Line Width
Ray

Settings:

Direction
Extend Forever
Price Label
Rectangle

Workflow:

Click
Drag
Release

Settings:

Fill Color
Fill Opacity
Border Color
Border Width
Border Style
Extend Right

Context Menu:

Lock
Clone
Send Back
Delete
Parallel Channel

Settings:

Line Color
Middle Line
Fill Color
Transparency
Regression Channel

Settings:

Deviation
Standard Deviation Bands
Color
Transparency
Fibonacci Retracement

Must replicate TradingView.

Workflow:

Point A
Point B
Auto Render Levels

Default Levels:

0
0.236
0.382
0.5
0.618
0.786
1
1.272
1.618
2.618

Settings:

Enable/Disable Levels
Custom Levels
Custom Labels
Reverse Fib
Log Scale
Background Fill

Toolbar:

Level Management
Color Presets
Fib Extension

Same TradingView behavior.

Settings:

Extensions
Labels
Colors
Reverse
Fixed Range Volume Profile

Must replicate TradingView workflow.

Workflow:

Select Tool
Drag Start Point
Drag End Point
Volume Profile Generated

Settings:

Rows
Value Area %
POC
VAH
VAL
Up Volume Color
Down Volume Color
Profile Width
Show Labels
Developing POC

Toolbar:

Color Settings
Value Area
POC
Visibility

Performance:

WebWorker Processing
Virtual Rendering
Text Tool

Settings:

Font
Size
Bold
Italic
Underline
Alignment
Background
Border
Text Color

Double click:

Edit Text
Text With Arrow

Settings:

Arrow Style
Arrow Color
Font
Text
Background
Note Tool

Support:

Sticky Notes
Multi-line
Markdown
Head and Shoulders Tool

Workflow:

Auto Pattern Shape
User Adjust Anchors

Settings:

Label Visibility
Target Projection
Pattern Color
Text Visibility
Long Position Tool

Must match TradingView.

Fields:

Entry
Stop Loss
Take Profit
Risk %
Account Size
Quantity
Risk Reward Ratio
PnL

Auto Calculate:

Risk
Reward
RR
Position Size

Resizable by dragging.

Short Position Tool

Same as Long Position.

Brush Tool

Settings:

Width
Opacity
Color
Eraser
Highlighter Tool

Settings:

Opacity
Glow
Width
Measure Tool

Workflow:

Click
Drag
Show:
Price Change
Percent Change
Bars
Time
Magnet Mode

Support:

Weak Magnet
Strong Magnet
Off

Snap to OHLC.

OBJECT MANAGER

Create TradingView-like object tree.

Features:

List All Drawings
Search
Lock
Hide
Delete
Group
Multi Select
SHORTCUTS

Implement TradingView-like shortcuts.

Delete = Delete Object

Ctrl + C = Copy

Ctrl + V = Paste

Ctrl + Z = Undo

Ctrl + Shift + Z = Redo

Alt + Drag = Clone

Esc = Cancel Current Tool

Shift = Straight Line Constraint

Ctrl + Click = Multi Select

Tab = Next Object

Shift + Tab = Previous Object

H = Horizontal Line

Alt + H = Horizontal Ray

V = Vertical Line

T = Text

R = Rectangle

P = Parallel Channel

F = Fib Retracement

M = Measure Tool

B = Brush

L = Trend Line
PERFORMANCE REQUIREMENTS

Support:

5000+ Drawings
Multiple Layouts
Autosave
Undo/Redo History
Object Persistence
Cloud Sync Ready

Use:

React
TypeScript
Canvas Rendering
RequestAnimationFrame
Web Workers

Goal:

Make drawing tool UX, workflow, settings panels, context menus, shortcuts, selection behavior, editing behavior, and object management match TradingView as closely as possible while remaining compatible with the existing platform architecture.

One additional recommendation: don't try to implement all 100+ TradingView tools at once. Prioritize:

Trend Line
Horizontal Line
Rectangle
Fib Retracement
Long/Short Position
Fixed Range Volume Profile
Text Tools
Channels

These 8 tools cover roughly 90% of what traders actually use.