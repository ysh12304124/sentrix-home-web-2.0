import type { MemorySpace } from "./api/client";

/** 相册的内部 id/占位名不能直接给用户看。真实名称来自后端列表。 */
export function spaceDisplayName(space: MemorySpace | undefined): string {
  if (space === undefined) return "默认相册";
  const name = space.name.trim();
  if (name === "" || name === space.id || name === "home-default") return "默认相册";
  return name;
}

export function scopeDisplayName(
  spaces: readonly MemorySpace[],
  scopeId: string,
): string {
  if (scopeId === "") return "全部相册";
  return spaceDisplayName(spaces.find((space) => space.id === scopeId));
}
