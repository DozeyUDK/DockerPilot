export function isCurrentKeyFileRead(reader, activeReader, generation, activeGeneration) {
  return reader === activeReader && generation === activeGeneration
}
