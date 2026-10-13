type SortValue = number | string | null

function readValue(cell?: HTMLTableCellElement): SortValue {
  if (!cell) return null
  const value = (cell.dataset.sortValue ?? cell.textContent ?? "").replace(/\s+/g, " ").trim()
  if (!value || /^(?:—|-|n\/?a)$/i.test(value)) return null
  if (/^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(value)) {
    const timestamp = Date.parse(value)
    if (Number.isFinite(timestamp)) return timestamp
  }
  const numeric = value.replace(/,/g, "").replace(/[%×x£$€]/gi, "").trim()
  if (/^[+-]?\d+(?:\.\d+)?$/.test(numeric)) return Number(numeric)
  return value.toLocaleLowerCase()
}

function compareValues(left: SortValue, right: SortValue, direction: "ascending" | "descending") {
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  const result = typeof left === "number" && typeof right === "number"
    ? left - right
    : String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" })
  return direction === "ascending" ? result : -result
}

function enhanceTable(table: HTMLTableElement) {
  if (table.dataset.sortableEnhanced === "true") return
  table.dataset.sortableEnhanced = "true"
  const headers = Array.from(table.querySelectorAll<HTMLTableCellElement>("thead th"))
  headers.forEach((header, columnIndex) => {
    const label = header.textContent?.replace(/\s+/g, " ").trim() ?? ""
    if (!label || /^(?:actions?|options?)$/i.test(label) || header.scope === "row" || header.dataset.sortable === "false") return
    if (header.hasAttribute("aria-sort") || header.querySelector("button, a, input, select, [role='button']") || header.classList.contains("cursor-pointer")) return

    const button = document.createElement("button")
    button.type = "button"
    button.className = "auto-sort-button"
    button.setAttribute("aria-label", `Sort by ${label}`)
    button.title = `Sort by ${label}`
    while (header.firstChild) button.appendChild(header.firstChild)
    const indicator = document.createElement("span")
    indicator.className = "auto-sort-indicator"
    indicator.setAttribute("aria-hidden", "true")
    indicator.textContent = "↕"
    button.appendChild(indicator)
    header.appendChild(button)

    button.addEventListener("click", () => {
      const direction = table.dataset.sortColumn === String(columnIndex) && table.dataset.sortDirection === "ascending"
        ? "descending"
        : "ascending"
      table.dataset.sortColumn = String(columnIndex)
      table.dataset.sortDirection = direction
      headers.forEach((candidate) => {
        const generatedButton = candidate.querySelector<HTMLButtonElement>(".auto-sort-button")
        if (generatedButton) {
          candidate.removeAttribute("aria-sort")
          generatedButton.removeAttribute("data-sort-direction")
        }
        const marker = candidate.querySelector<HTMLElement>(".auto-sort-indicator")
        if (marker) marker.textContent = "↕"
      })
      header.setAttribute("aria-sort", direction)
      button.dataset.sortDirection = direction
      indicator.textContent = direction === "ascending" ? "↑" : "↓"
      for (const body of Array.from(table.tBodies)) {
        Array.from(body.rows)
          .map((row, index) => ({ row, index, value: readValue(row.cells[columnIndex]) }))
          .sort((left, right) => compareValues(left.value, right.value, direction) || left.index - right.index)
          .forEach(({ row }) => body.appendChild(row))
      }
    })
  })
}

export function installTableSorters() {
  const enhance = () => document.querySelectorAll<HTMLTableElement>("main table").forEach(enhanceTable)
  enhance()
  const observer = new MutationObserver(enhance)
  const root = document.body
  if (root) observer.observe(root, { childList: true, subtree: true })
  return () => observer.disconnect()
}
