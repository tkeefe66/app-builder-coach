const KIND: Record<string, string> = {
  "used": "chip-good",
  "configured-but-unused": "chip-warn",
};

export default function StatusChip({ status }: { status: string }) {
  return <span className={`chip ${KIND[status] ?? "chip-neutral"}`}>{status}</span>;
}
