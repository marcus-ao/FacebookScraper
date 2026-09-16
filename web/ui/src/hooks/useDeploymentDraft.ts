import { useEffect, useId, useLayoutEffect } from 'react'
import { deploymentStore } from '@/app/deployment-store'
import { applyBeforeUnload } from '@/lib/unsaved-changes'

/** Each editor owns a dirty source; the shell protects navigation across the aggregate. */
export function useDeploymentDraft(dirty: boolean): void {
  const source = useId()
  useLayoutEffect(() => { deploymentStore.setDirty(source, dirty) }, [source, dirty])
  useLayoutEffect(() => () => { deploymentStore.setDirty(source, false) }, [source])
  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => applyBeforeUnload(event, true)
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])
}
