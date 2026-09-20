export function nextModalFocusIndex(currentIndex, itemCount, backwards = false) {
  if (!Number.isInteger(itemCount) || itemCount <= 0) return -1
  if (!Number.isInteger(currentIndex) || currentIndex < 0 || currentIndex >= itemCount) {
    return backwards ? itemCount - 1 : 0
  }
  return (currentIndex + (backwards ? -1 : 1) + itemCount) % itemCount
}
