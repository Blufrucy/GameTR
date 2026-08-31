/**
 * 虚拟化性能验证（M4 双栏编辑器 5 万行目标）。
 *
 * 核心证明：@tanstack/react-virtual 只计算/渲染可视区行，与数据量无关——
 * 50000 行数据，虚拟项数 ≈ 视口行数 + overscan（~28），不随数据增长。
 * 这正是「内存少 + 流畅」的来源（DOM 只有 ~30 行）。
 *
 * 滚动位置正确性由 @tanstack/react-virtual 官方验证（5 万行示例）；
 * 本测试聚焦「内存/渲染与数据量解耦」这一 M4 性能前提。
 */

import { describe, expect, it } from "vitest";
import { Virtualizer, elementScroll } from "@tanstack/virtual-core";

/** 模拟滚动容器：clientHeight 固定（jsdom 无布局，用假元素）。 */
function fakeScrollEl(clientHeight = 800) {
  const el = { scrollTop: 0, clientHeight, scrollWidth: 1200, clientWidth: 1200 } as {
    scrollTop: number;
    clientHeight: number;
    clientWidth: number;
    scrollWidth: number;
  };
  return el;
}

function makeVirtualizer(count: number, el: { scrollTop: number; clientHeight: number }) {
  return new Virtualizer({
    count,
    getScrollElement: () => el as unknown as HTMLElement,
    estimateSize: () => 40, // 固定行高 40px
    overscan: 8,
    initialRect: { width: 1200, height: el.clientHeight }, // 无 DOM 布局，用 initialRect 提供视口
    scrollToFn: elementScroll,
    observeElementRect: (_, cb) => {
      cb({ width: 1200, height: el.clientHeight });
      return () => {};
    },
    observeElementOffset: (_, cb) => {
      cb(el.scrollTop, false);
      return () => {};
    },
  });
}

describe("VirtualTable 虚拟化（5 万行性能）", () => {
  it("50000 行只计算可视区行（~20 + overscan），不随数据量增长", () => {
    const el = fakeScrollEl(800); // 视口 800px / 40px = 20 行
    const v = makeVirtualizer(50_000, el);
    const items = v.getVirtualItems();
    // 可视 20 行 + overscan → 远小于数据量
    expect(items.length).toBeGreaterThan(0);
    expect(items.length).toBeLessThan(60);
    expect(v.getTotalSize()).toBe(50_000 * 40); // 滚动条总高度 = 全量数据
  });

  it("数据量翻倍不增加渲染行数（内存与数据量解耦）", () => {
    const small = makeVirtualizer(5_000, fakeScrollEl(800));
    const large = makeVirtualizer(500_000, fakeScrollEl(800));
    const smallN = small.getVirtualItems().length;
    const largeN = large.getVirtualItems().length;
    expect(largeN).toBe(smallN); // 100 倍数据量，渲染行数不变
  });

  it("虚拟项索引连续且覆盖可视区（0 起递增）", () => {
    const v = makeVirtualizer(10_000, fakeScrollEl(800));
    const items = v.getVirtualItems();
    const indexes = items.map((i) => i.index);
    expect(indexes).toEqual([...indexes].sort((a, b) => a - b)); // 递增
    expect(items[0].index).toBe(0); // 顶部从第 0 行起
  });
});
