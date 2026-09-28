import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type SortDirection = 'asc' | 'desc'
export type SortValue = string | number | null | undefined
export type SortAccessors<T, K extends string> = Record<K, (row: T) => SortValue>

/** Numeric order for band labels such as "<50", "85-90" or "-10 to -5pp". */
export function bandSortValue(label: string): number {
  if (label.startsWith('<')) return Number.NEGATIVE_INFINITY
  if (label.startsWith('>')) return Number.POSITIVE_INFINITY
  const match = label.match(/-?\d+(\.\d+)?/)
  return match ? Number(match[0]) : Number.NaN
}

export function compareSortValues(a: SortValue, b: SortValue): number {
  const aMissing = a == null || (typeof a === 'number' && Number.isNaN(a))
  const bMissing = b == null || (typeof b === 'number' && Number.isNaN(b))
  if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
}

/**
 * Client-side sorting for a table. Clicking a new column sorts text A→Z and
 * numbers high→low; clicking it again reverses. Missing values always sort last.
 */
export function useSortableRows<T, K extends string>(
  rows: T[],
  accessors: SortAccessors<T, K>,
  initial: { key: NoInfer<K>; direction: SortDirection } | null = null,
) {
  const [sort, setSort] = useState(initial)
  const sorted = useMemo(() => {
    if (!sort) return rows
    const accessor = accessors[sort.key]
    return rows
      .map((row, index) => ({ row, index, value: accessor(row) }))
      .sort((a, b) => {
        const aMissing = a.value == null, bMissing = b.value == null
        if (aMissing || bMissing) return aMissing === bMissing ? a.index - b.index : aMissing ? 1 : -1
        const compared = compareSortValues(a.value, b.value)
        return (sort.direction === 'asc' ? compared : -compared) || a.index - b.index
      })
      .map(item => item.row)
    // accessors are recreated each render; the sort only depends on the key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort])

  const toggle = (key: K) => setSort(current => {
    if (current?.key === key) return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
    const sample = rows.map(accessors[key]).find(value => value != null)
    return { key, direction: typeof sample === 'number' ? 'desc' : 'asc' }
  })

  const header = (key: K, label: ReactNode, className?: string) => (
    <SortableTh key={key} label={label} direction={sort?.key === key ? sort.direction : null} onSort={() => toggle(key)} className={className} />
  )

  return { sorted, sort, toggle, header }
}

export function SortableTh({ label, direction, onSort, className }: { label: ReactNode; direction: SortDirection | null; onSort: () => void; className?: string }) {
  return (
    <th className={className} aria-sort={direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none'}>
      <button type="button" className="table-sort" onClick={onSort}>
        {label} <span aria-hidden="true">{direction === 'asc' ? '▲' : direction === 'desc' ? '▼' : '↕'}</span>
      </button>
    </th>
  )
}
