export const buildServerOptions = (servers = []) => [
  {
    id: 'local',
    name: 'Local',
    detail: 'This DockerPilot host',
    icon: '🏠',
  },
  ...servers.map(server => ({
    id: server.id,
    name: server.name || server.id,
    label: `${server.name || server.id} (${server.hostname})`,
    detail: `${server.username}@${server.hostname}:${server.port}`,
    icon: '🖥️',
  })),
]

export const getServerOptionLabel = (serverId, options) => {
  const option = options.find(candidate => candidate.id === serverId)
  return option ? option.label || option.name : serverId
}

export const getNextServerOptionIndex = (currentIndex, optionCount, key) => {
  if (!Number.isInteger(optionCount) || optionCount <= 0) {
    return -1
  }

  if (key === 'Home') {
    return 0
  }
  if (key === 'End') {
    return optionCount - 1
  }
  if (key === 'ArrowDown') {
    return currentIndex < 0 ? 0 : (currentIndex + 1) % optionCount
  }
  if (key === 'ArrowUp') {
    return currentIndex < 0 ? optionCount - 1 : (currentIndex - 1 + optionCount) % optionCount
  }

  return currentIndex
}
