import { useEffect, useId, useRef } from 'react'

import { nextModalFocusIndex } from '../utils/modalFocus.mjs'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function Modal({ open, onClose, title, children, size = 'medium' }) {
  const titleId = useId()
  const dialogRef = useRef(null)
  const previousFocusRef = useRef(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return undefined

    previousFocusRef.current = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const animationFrame = window.requestAnimationFrame(() => {
      const firstFocusable = dialogRef.current?.querySelector(FOCUSABLE_SELECTOR)
      ;(firstFocusable || dialogRef.current)?.focus()
    })

    const handleKeyDown = event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }

      if (event.key !== 'Tab' || !dialogRef.current) return

      const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR)]
      const currentIndex = focusable.indexOf(document.activeElement)
      const nextIndex = nextModalFocusIndex(currentIndex, focusable.length, event.shiftKey)
      if (nextIndex === -1) {
        event.preventDefault()
        dialogRef.current.focus()
        return
      }

      const wouldLeaveDialog = currentIndex === -1
        || (!event.shiftKey && currentIndex === focusable.length - 1)
        || (event.shiftKey && currentIndex === 0)
      if (wouldLeaveDialog) {
        event.preventDefault()
        focusable[nextIndex].focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(animationFrame)
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      if (previousFocusRef.current?.isConnected) previousFocusRef.current?.focus()
    }
  }, [open])

  if (!open) return null

  const handleBackdropMouseDown = event => {
    if (event.target !== event.currentTarget) return
    onClose()
  }

  return (
    <div className="modal-backdrop" onMouseDown={handleBackdropMouseDown}>
      <section
        ref={dialogRef}
        className={`modal-dialog modal-dialog-${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal-header">
          <h2 id={titleId} className="modal-title">{title}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label={`Close ${title}`}
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  )
}

export default Modal
