# Microsoft Excel guide

Written for Microsoft 365 Excel and Excel 2021. Functions marked "new" need Microsoft 365 or Excel 2021 and later.

## Basics

A **workbook** has **sheets** (tabs at the bottom). A sheet is a grid of **cells** named by column and row, like B3. A **formula** starts with an equals sign. A
**range** is a block of cells such as B2:B10. The **ribbon** along the top has the commands; the **formula bar** shows what is in the selected cell.

## Do this

### Add up numbers
1. Click the cell where the total goes.
2. Type `=SUM(` then select the range with the mouse, type `)`, press Enter. Or click a cell under a column and press Alt+= to sum it automatically.

### Average, count, biggest and smallest
`=AVERAGE(B2:B10)`, `=COUNT(B2:B10)` (numbers only), `=COUNTA(B2:B10)` (anything not empty), `=MAX(B2:B10)`, `=MIN(B2:B10)`.

### Add only the numbers that meet a condition
`=SUMIF(A2:A100,"Food",B2:B100)` adds column B where column A says Food. With several conditions: `=SUMIFS(B2:B100,A2:A100,"Food",C2:C100,">=2026-01-01")`.
Count rows that match: `=COUNTIF(A2:A100,"Food")`.

### Look up a value
New: `=XLOOKUP(E2, A2:A100, C2:C100, "not found")` finds E2 in column A and returns column C.
Older: `=VLOOKUP(E2, A2:C100, 3, FALSE)` (the FALSE means exact match; the lookup value must be in the first column of the range).
If a lookup shows #N/A, the value is not there or has a stray space; try `=TRIM(E2)`.

### Do something if...
`=IF(B2>=50,"Pass","Fail")`. Nest more checks with `=IFS(B2>=90,"A",B2>=80,"B",TRUE,"C")`. Show something else instead of an error: `=IFERROR(A2/B2,0)`.

### Fix formulas that change when copied
A **$** locks part of a reference. `$B$2` always points at B2; `B$2` locks the row only. Press F4 while editing a reference to cycle through the choices.

### Make a table that sorts and filters
1. Click inside your data (with headers in the top row).
2. Press Ctrl+T, tick **My table has headers**, **OK**.
3. Use the arrows on the headers to sort or filter. Formulas in a table fill down automatically.

### Sort and filter plain data
**Data, Sort** (pick the column and order) or **Data, Filter** (arrows on the headers). Select the whole data block first so rows stay together.

### Remove duplicates
Select the range, **Data, Remove Duplicates**, tick the columns that make a row a duplicate.

### Make a chart
Select the data including the labels, **Insert, Recommended Charts** (or choose a column, line or pie chart). Click the chart and use **Chart Design** and **Format** to adjust it.

### Summarise a lot of data with a PivotTable
1. Click inside the data, **Insert, PivotTable**, **OK**.
2. Drag a text field (such as Category) into **Rows**, a number field (such as Amount) into **Values**.
3. Drag a date or another field into **Columns** or **Filters** to slice further. Right-click a value to change Sum to Average or Count.
Press **Refresh** (Analyze tab) after the data changes.

### Clean up text
`=TRIM(A2)` removes extra spaces, `=UPPER()`, `=LOWER()`, `=PROPER()` change case, `=LEFT(A2,3)`, `=MID(A2,2,4)`, `=RIGHT(A2,2)` cut text, `=TEXTJOIN(", ",TRUE,A2:A6)` joins. New: `=TEXTSPLIT(A2,",")`.
To split a column into several: **Data, Text to Columns**.

### Dates
Dates are numbers. `=TODAY()` is today, `=B2-A2` is days between, `=DATEDIF(A2,B2,"m")` is whole months, `=EOMONTH(A2,0)` is month end, `=TEXT(A2,"ddd d mmm")` formats as text.

### Freeze the top row, protect or hide
**View, Freeze Panes, Freeze Top Row**. Protect a sheet from edits: **Review, Protect Sheet**. Right-click a column letter to hide it.

### Save as PDF or CSV
**File, Save As** and choose **PDF** or **CSV UTF-8 (Comma delimited)**. CSV keeps only the current sheet and no formatting.

## Shortcuts

| Keys | What it does |
|---|---|
| Ctrl+Arrow | Jump to the edge of the data |
| Ctrl+Shift+Arrow | Select to the edge of the data |
| Ctrl+; | Insert today's date |
| Ctrl+D / Ctrl+R | Fill down / right |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| F2 | Edit the cell |
| Ctrl+` | Show formulas instead of results |
| Alt+Enter | New line inside a cell |
| Ctrl+Shift+L | Turn filters on or off |

## When it goes wrong

- **####:** the column is too narrow (double-click the column edge) or a date/time is negative.
- **#NAME?:** a function is mistyped or not available in your version.
- **#VALUE!:** the formula is mixing text and numbers; look for stray spaces or text that looks like a number (Data, Text to Columns, Finish, converts it).
- **#REF!:** a cell the formula used was deleted. Undo, or fix the reference.
- **#DIV/0!:** dividing by zero or an empty cell; wrap in `IFERROR`.
- **Numbers won't add:** they are stored as text (a green triangle appears); select and choose **Convert to Number**.
- **The formula shows as text:** the cell is formatted as Text; set it to General, press F2, Enter.
- **Calculations are not updating:** **Formulas, Calculation Options, Automatic**.
- **File is slow or huge:** remove unused formatting (**Home, Clear, Clear Formats** on empty rows), avoid whole-column ranges in many formulas.

## Where to look next

Microsoft Support (support.microsoft.com/excel) and the function reference there; type a function name in the search box.
