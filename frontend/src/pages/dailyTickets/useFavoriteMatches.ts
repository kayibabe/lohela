import { useCallback, useEffect, useMemo, useState } from 'react'

const STORAGE_KEY = 'lohela.match-favorites.v1'

export function useFavoriteMatches() {
  const [favorites, setFavorites] = useState<number[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      const parsed: unknown = saved ? JSON.parse(saved) : []
      return Array.isArray(parsed)
        ? [...new Set(parsed.filter((id): id is number => Number.isSafeInteger(id) && id > 0))]
        : []
    } catch {
      return []
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites))
    } catch {
      // Keep favourites usable for the current view when browser storage is unavailable.
    }
  }, [favorites])

  const favoriteIds = useMemo(() => new Set(favorites), [favorites])
  const toggleFavorite = useCallback((matchId: number) => {
    setFavorites(current => current.includes(matchId)
      ? current.filter(id => id !== matchId)
      : [...current, matchId])
  }, [])

  return { favorites, favoriteIds, toggleFavorite }
}
