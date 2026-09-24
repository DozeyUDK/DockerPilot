import React, { useEffect, useId, useRef, useState } from 'react'
import { useServer } from '../contexts/ServerContext'
import {
  buildServerOptions,
  getNextServerOptionIndex,
  getServerOptionLabel,
} from '../utils/serverSelector.mjs'

function ServerSelector() {
  const { servers, selectedServer, selectedServerInfo, loading, selectServer } = useServer()
  const [isOpen, setIsOpen] = useState(false)
  const [selectionError, setSelectionError] = useState('')
  const menuId = useId()
  const selectorRef = useRef(null)
  const triggerRef = useRef(null)
  const optionRefs = useRef([])
  const options = buildServerOptions(servers)

  useEffect(() => {
    if (!isOpen) {
      return undefined
    }

    const selectedIndex = Math.max(0, options.findIndex(option => option.id === selectedServer))
    optionRefs.current[selectedIndex]?.focus()

    const handlePointerDown = (event) => {
      if (!selectorRef.current?.contains(event.target)) {
        setIsOpen(false)
      }
    }
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setIsOpen(false)
        triggerRef.current?.focus()
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [isOpen, selectedServer, options.length])

  const handleServerChange = async (serverId) => {
    setSelectionError('')
    const result = await selectServer(serverId, false)
    if (result.success) {
      setIsOpen(false)
      triggerRef.current?.focus()
    } else {
      setSelectionError(result.error || 'Unable to select server')
    }
  }

  const toggleMenu = () => {
    setSelectionError('')
    setIsOpen(open => !open)
  }

  const handleMenuKeyDown = (event) => {
    if (event.key === 'Tab') {
      setIsOpen(false)
      return
    }

    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      return
    }

    event.preventDefault()
    const availableOptions = optionRefs.current.filter(Boolean)
    const currentIndex = availableOptions.indexOf(document.activeElement)
    const nextIndex = getNextServerOptionIndex(currentIndex, availableOptions.length, event.key)
    availableOptions[nextIndex]?.focus()
  }

  return (
    <div className="server-selector" ref={selectorRef}>
      <button
        type="button"
        ref={triggerRef}
        onClick={toggleMenu}
        disabled={loading}
        className="server-selector-trigger"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-controls={isOpen ? menuId : undefined}
        title={selectedServerInfo ? `${selectedServerInfo.username}@${selectedServerInfo.hostname}:${selectedServerInfo.port}` : 'Select server'}
      >
        <span aria-hidden="true">🖥️</span>
        <span className="server-selector-label">{getServerOptionLabel(selectedServer, options)}</span>
        <span aria-hidden="true">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <div
          id={menuId}
          className="server-selector-menu"
          role="menu"
          aria-label="Select server"
          onKeyDown={handleMenuKeyDown}
        >
          <div className="server-selector-heading" aria-hidden="true">Select server</div>
          {options.map((option, index) => (
            <button
              type="button"
              role="menuitemradio"
              aria-checked={selectedServer === option.id}
              className="server-selector-option"
              key={option.id}
              ref={element => { optionRefs.current[index] = element }}
              disabled={loading}
              onClick={() => handleServerChange(option.id)}
            >
              <span aria-hidden="true">{option.icon}</span>
              <span className="server-selector-option-copy">
                <strong>{option.name}</strong>
                <small>{option.detail}</small>
              </span>
              {selectedServer === option.id && <span aria-hidden="true">✓</span>}
            </button>
          ))}
          {selectionError && (
            <div className="server-selector-error" role="alert">{selectionError}</div>
          )}
        </div>
      )}
    </div>
  )
}

export default ServerSelector
