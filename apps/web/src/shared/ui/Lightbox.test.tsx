import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Lightbox } from "./Lightbox";

afterEach(cleanup);

describe("Lightbox", () => {
  it("多图可点下一张并关闭", () => {
    const onClose = vi.fn();
    render(
      <Lightbox
        items={[
          { src: "/a.jpg", caption: "A" },
          { src: "/b.jpg", caption: "B" },
        ]}
        onClose={onClose}
      />,
    );

    expect(document.querySelector<HTMLImageElement>(".lightbox img")?.getAttribute("src")).toBe("/a.jpg");
    fireEvent.click(screen.getByRole("button", { name: "下一张" }));
    expect(document.querySelector<HTMLImageElement>(".lightbox img")?.getAttribute("src")).toBe("/b.jpg");
    expect(screen.getByText(/2 \/ 2/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "关闭图片预览" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("横向滑动可切图，且不会误触发关闭", () => {
    const onClose = vi.fn();
    render(
      <Lightbox
        items={[
          { src: "/a.jpg" },
          { src: "/b.jpg" },
        ]}
        onClose={onClose}
      />,
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.touchStart(dialog, { touches: [{ clientX: 300 }] });
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 100 }] });
    expect(document.querySelector<HTMLImageElement>(".lightbox img")?.getAttribute("src")).toBe("/b.jpg");
    fireEvent.click(dialog);
    expect(onClose).not.toHaveBeenCalled();
  });
});
