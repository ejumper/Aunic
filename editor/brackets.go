package editor

var bracePairs = [][2]rune{
	{'(', ')'},
	{'{', '}'},
	{'[', ']'},
}

type bracketMatch struct {
	aRow, aCol int // open bracket (rune offset)
	bRow, bCol int // close bracket (rune offset)
}

// findBracketPair checks the rune at (curRow, curCol) and, as a fallback, at
// (curRow, curCol-1). Returns the positions of both brackets when a matched
// pair is found. aRow/aCol always point to the open bracket, bRow/bCol to the
// close bracket, regardless of cursor direction.
func findBracketPair(lines []string, curRow, curCol int) (bracketMatch, bool) {
	for _, col := range []int{curCol, curCol - 1} {
		if col < 0 {
			continue
		}
		if curRow >= len(lines) {
			continue
		}
		runes := []rune(lines[curRow])
		if col >= len(runes) {
			continue
		}
		ch := runes[col]
		for _, pair := range bracePairs {
			switch ch {
			case pair[0]:
				if mRow, mCol, ok := bracketSearchForward(lines, curRow, col, pair); ok {
					return bracketMatch{curRow, col, mRow, mCol}, true
				}
			case pair[1]:
				if mRow, mCol, ok := bracketSearchBackward(lines, curRow, col, pair); ok {
					return bracketMatch{mRow, mCol, curRow, col}, true
				}
			}
		}
	}
	return bracketMatch{}, false
}

// bracketSearchForward scans forward from (startRow, startCol) using a nesting
// counter. Returns the position of the matching close bracket when depth reaches 0.
func bracketSearchForward(lines []string, startRow, startCol int, pair [2]rune) (int, int, bool) {
	depth := 0
	for y := startRow; y < len(lines); y++ {
		runes := []rune(lines[y])
		xInit := 0
		if y == startRow {
			xInit = startCol
		}
		for x := xInit; x < len(runes); x++ {
			switch runes[x] {
			case pair[0]:
				depth++
			case pair[1]:
				depth--
				if depth == 0 {
					return y, x, true
				}
			}
		}
	}
	return 0, 0, false
}

// bracketSearchBackward scans backward from (startRow, startCol).
func bracketSearchBackward(lines []string, startRow, startCol int, pair [2]rune) (int, int, bool) {
	depth := 0
	for y := startRow; y >= 0; y-- {
		runes := []rune(lines[y])
		xInit := len(runes) - 1
		if y == startRow {
			xInit = startCol
		}
		for x := xInit; x >= 0; x-- {
			switch runes[x] {
			case pair[1]:
				depth++
			case pair[0]:
				depth--
				if depth == 0 {
					return y, x, true
				}
			}
		}
	}
	return 0, 0, false
}
