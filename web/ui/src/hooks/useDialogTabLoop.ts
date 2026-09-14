import { useEffect } from 'react'

// 补首尾 Tab 循环；进入、Esc 和焦点恢复仍由弹层处理。
export function useDialogTabLoop() {
  useEffect(() => {
    const loop = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !(document.activeElement instanceof HTMLElement)) return
      const dialog = document.activeElement.closest('[role="dialog"][aria-modal="true"]')
      if (!dialog) return
      const items = [...dialog.querySelectorAll<HTMLElement>('button,input,textarea,select,a[href],[tabindex],[contenteditable="true"]')]
        .filter(item => item.tabIndex >= 0 && !item.matches(':disabled') && item.getClientRects().length > 0 && getComputedStyle(item).visibility !== 'hidden')
      const first = items[0], last = items.at(-1)
      const target = event.shiftKey && document.activeElement === first ? last : !event.shiftKey && document.activeElement === last ? first : null
      if (target) { event.preventDefault(); target.focus({ preventScroll: true }) }
    }
    document.addEventListener('keydown', loop, true)
    return () => document.removeEventListener('keydown', loop, true)
  }, [])
}
