// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { bandSortValue, compareSortValues, useSortableRows } from './SortableTable'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Row { name: string; roi: number | null; band: string }
const ROWS: Row[] = [
  { name: 'beta', roi: 0.1, band: '70-75' },
  { name: 'Alpha', roi: null, band: '<50' },
  { name: 'gamma', roi: -0.2, band: '85-90' },
]

function Table({ rows }: { rows: Row[] }) {
  const { sorted, header } = useSortableRows(rows, { name: row => row.name, roi: row => row.roi, band: row => bandSortValue(row.band) })
  return <table><thead><tr>{header('name', 'Name')}{header('roi', 'ROI')}{header('band', 'Band')}</tr></thead><tbody>{sorted.map(row => <tr key={row.name}><td>{row.name}</td></tr>)}</tbody></table>
}

let container: HTMLDivElement
afterEach(() => container?.remove())

function render() {
  container = document.createElement('div')
  document.body.appendChild(container)
  act(() => createRoot(container).render(<Table rows={ROWS} />))
  const names = () => Array.from(container.querySelectorAll('tbody td')).map(cell => cell.textContent)
  const th = (index: number) => container.querySelectorAll('th')[index]
  const click = (index: number) => act(() => th(index).querySelector('button')!.click())
  return { names, th, click }
}

describe('useSortableRows', () => {
  it('keeps the original order until a header is clicked', () => {
    const { names, th } = render()
    expect(names()).toEqual(['beta', 'Alpha', 'gamma'])
    expect(th(0).getAttribute('aria-sort')).toBe('none')
  })

  it('sorts text A→Z first, case-insensitively, then reverses', () => {
    const { names, th, click } = render()
    click(0)
    expect(names()).toEqual(['Alpha', 'beta', 'gamma'])
    expect(th(0).getAttribute('aria-sort')).toBe('ascending')
    click(0)
    expect(names()).toEqual(['gamma', 'beta', 'Alpha'])
    expect(th(0).getAttribute('aria-sort')).toBe('descending')
  })

  it('sorts numbers high→low first and keeps missing values last in both directions', () => {
    const { names, click } = render()
    click(1)
    expect(names()).toEqual(['beta', 'gamma', 'Alpha'])
    click(1)
    expect(names()).toEqual(['gamma', 'beta', 'Alpha'])
  })

  it('orders probability bands numerically with "<50" lowest', () => {
    const { names, click } = render()
    click(2)
    click(2)
    expect(names()).toEqual(['Alpha', 'beta', 'gamma'])
  })
})

describe('sort helpers', () => {
  it('parses band labels', () => {
    expect(bandSortValue('85-90')).toBe(85)
    expect(bandSortValue('-10 to -5pp')).toBe(-10)
    expect(bandSortValue('< -10pp')).toBe(Number.NEGATIVE_INFINITY)
    expect(bandSortValue('>= 15pp')).toBe(Number.POSITIVE_INFINITY)
    expect(Number.isNaN(bandSortValue('Unknown'))).toBe(true)
  })

  it('compares mixed values with missing last', () => {
    expect(compareSortValues(1, 2)).toBeLessThan(0)
    expect(compareSortValues('item 2', 'item 10')).toBeLessThan(0)
    expect(compareSortValues(null, 1)).toBeGreaterThan(0)
  })
})
