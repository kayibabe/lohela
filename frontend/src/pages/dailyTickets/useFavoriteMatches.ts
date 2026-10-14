import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAuth } from '../../auth'
import { addMatchFavorite, fetchMatchFavoriteIds, removeMatchFavorite } from '../../lib/api'

const STORAGE_KEY = 'lohela.match-favorites.v2'

function readStoredFavorites(key: string): number[] {
  try {
    const saved = localStorage.getItem(key)
    const parsed: unknown = saved ? JSON.parse(saved) : []
    return Array.isArray(parsed)
      ? [...new Set(parsed.filter((id): id is number => Number.isSafeInteger(id) && id > 0))]
      : []
  } catch {
    return []
  }
}

export function useFavoriteMatches() {
  const { user, loading: authLoading } = useAuth()
  const [favorites, setFavorites] = useState<number[]>([])
  const [remoteReady, setRemoteReady] = useState(false)
  const storageKey = `${STORAGE_KEY}:${user ? `user-${user.id}` : 'guest'}`

  useEffect(() => {
    if (authLoading) return
    let active = true
    setRemoteReady(false)
    setFavorites(readStoredFavorites(storageKey))
    if (!user) return () => { active = false }

    fetchMatchFavoriteIds().then(ids => {
      if (!active) return
      setFavorites(ids)
      setRemoteReady(true)
    }).catch(() => {
      if (active) setRemoteReady(true)
    })
    return () => { active = false }
  }, [authLoading, storageKey, user])

  useEffect(() => {
    if (authLoading) return
    try {
      localStorage.setItem(storageKey, JSON.stringify(favorites))
    } catch {
      // Keep favourites usable for the current view when browser storage is unavailable.
    }
  }, [authLoading, favorites, storageKey])

  const favoriteIds = useMemo(() => new Set(favorites), [favorites])
  const toggleFavorite = useCallback((matchId: number) => {
    const adding = !favoriteIds.has(matchId)
    setFavorites(current => current.includes(matchId)
      ? current.filter(id => id !== matchId)
      : [...current, matchId])
    if (!user || !remoteReady) return
    const update = adding ? addMatchFavorite(matchId) : removeMatchFavorite(matchId)
    update.then(setFavorites).catch(() => setRemoteReady(false))
  }, [favoriteIds, remoteReady, user])

  return { favorites, favoriteIds, toggleFavorite }
}
