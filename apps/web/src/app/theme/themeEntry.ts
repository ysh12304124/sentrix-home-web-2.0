/** Production appearance changes are restricted to the consumer gateway on 8210. */
export function isThemeEntry(
  location: Pick<Location, "hostname" | "port"> = window.location,
  development = import.meta.env.DEV,
): boolean {
  if (location.port === "8210") return true;
  return development && ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
    && ["4274", "4275"].includes(location.port);
}
