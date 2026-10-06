export function runtimeNeedsUpdate(status) {
  const expectedVersion = status?.manifest?.version;
  const expectedChecksum = status?.manifest?.runtimeSha256;
  return Boolean(
    (expectedVersion && status.installedVersion !== expectedVersion)
    || (expectedChecksum && status.installedSha256 !== expectedChecksum),
  );
}
