// Prove what er-crash-logging records for a STATUS_STACK_OVERFLOW (2026-10-26: its overflow record
// walks the stack from a helper thread). The game dies from this; run it only against an
// agent-owned launch.
//
// Under Wine a thread that genuinely exhausts its stack disappears without any exception reaching
// a handler: measured 2026-10-26 on run br-20261026-201533-87a6, the recursing thread was gone, the
// game kept running, and no record was written. Windows does deliver it (a user's log from that
// day carries the registers of one). So this raises the code itself, from a thread 300 frames deep
// in a fixed three-function cycle, a -> b -> c -> a: the handler sees the real code on a real deep
// stack, and its `overflow_hot` line should list the three return sites with 100 each.
const source = `
extern void RaiseException(unsigned int code, unsigned int flags, unsigned int nargs,
                           const void *args);
int ovf_b(int n);
int ovf_c(int n);
int ovf_a(int n) {
  volatile char buf[1024];
  for (int i = 0; i < 1024; i++) buf[i] = (char)(n + i);
  if (n >= 300) { RaiseException(0xC00000FDu, 0, 0, 0); }
  return ovf_b(n + 1) + buf[n & 1023];
}
int ovf_b(int n) {
  volatile char buf[1024];
  for (int i = 0; i < 1024; i++) buf[i] = (char)(n + i);
  return ovf_c(n + 1) + buf[n & 1023];
}
int ovf_c(int n) {
  volatile char buf[1024];
  for (int i = 0; i < 1024; i++) buf[i] = (char)(n + i);
  return ovf_a(n + 1) + buf[n & 1023];
}
unsigned int ovf_entry(void *unused) {
  return (unsigned int)ovf_a(0);
}
`;

const cm = new CModule(source, {
  RaiseException: Module.getGlobalExportByName('RaiseException'),
});
const createThread = new NativeFunction(
  Module.getGlobalExportByName('CreateThread'),
  'pointer',
  ['pointer', 'size_t', 'pointer', 'pointer', 'uint32', 'pointer'],
);
send({
  event: 'overflow-module',
  ovf_a: cm.ovf_a.toString(),
  ovf_b: cm.ovf_b.toString(),
  ovf_c: cm.ovf_c.toString(),
});
const handle = createThread(ptr(0), 0, cm.ovf_entry, ptr(0), 0, ptr(0));
send({ event: 'overflow-thread-started', handle: handle.toString() });
