import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EvidenceValue, Pager, StatusBadge } from "../components/ui";

describe("components", () => {
  it("renders hostile evidence as text with automatic direction", () => {
    const { container } = render(<EvidenceValue value={"<script>alert('x')</script>"} />);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert('x')</script>")).toHaveAttribute("dir", "auto");
  });
  it("shows Persian text unchanged", () => {
    render(<EvidenceValue value="مدیر سیستم" />);
    expect(screen.getByText("مدیر سیستم")).toBeInTheDocument();
  });
  it("status badges carry text, not only colour", () => {
    render(<StatusBadge value="source_failed" />);
    expect(screen.getByText("Source failed")).toHaveClass("badge", "bad");
  });
  it("pager is keyboard operable and bounded", async () => {
    const onChange = vi.fn();
    render(<Pager offset={0} limit={50} total={120} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    screen.getByRole("button", { name: "Next" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith(50);
    expect(screen.getByText("1–50 of 120")).toBeInTheDocument();
  });
});
