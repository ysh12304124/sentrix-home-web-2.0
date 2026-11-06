import { describe, expect, it } from "vitest";
import { imageUrl, decorateMedia } from "./media.ts";

describe("历史结果媒体地址", () => {
  it("相册原始媒体不依赖当前产品库资产ID", () => {
    const [media] = decorateMedia([{asset_id: "old-db-asset", file_name: "receipt.jpg"}], "atm-smoke");
    expect(imageUrl(media)).toBe("/api/v1/albums/atm-smoke/photos/receipt.jpg");
  });
  it("无相册来源的资产使用代理", () => {
    expect(imageUrl({asset_id: "a"})).toBe("/api/v1/assets/a/file");
  });
});
