// Time the in-process steps a DXIL vertex-shader patch costs per shader, with libdxcompiler loaded
// once, the way the er-shader-compiler helper would hold it: IDxcCompiler::Disassemble of the
// game container to LLVM IR text, IDxcAssembler::AssembleToContainer of that text, and
// IDxcValidator::Validate in place (which signs). The text edit itself is not timed.
//
// Usage: dxa_time <a.dxil> [<b.dxil> ...]
// Prints one line per file: bytes, then median and maximum milliseconds of each step over 5 runs.
#include <dxc/WinAdapter.h>
#include <dxc/dxcapi.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

static std::string read_all(const char *p) {
  std::ifstream f(p, std::ios::binary);
  std::stringstream s;
  s << f.rdbuf();
  return s.str();
}

using Clock = std::chrono::steady_clock;

static double ms_since(Clock::time_point t0) {
  return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

int main(int argc, char **argv) {
  IDxcUtils *utils = nullptr;
  IDxcCompiler *compiler = nullptr;
  IDxcAssembler *asmr = nullptr;
  IDxcValidator *val = nullptr;
  auto t_load = Clock::now();
  if (FAILED(DxcCreateInstance(CLSID_DxcUtils, __uuidof(IDxcUtils), (void **)&utils)) ||
      FAILED(DxcCreateInstance(CLSID_DxcCompiler, __uuidof(IDxcCompiler), (void **)&compiler)) ||
      FAILED(DxcCreateInstance(CLSID_DxcAssembler, __uuidof(IDxcAssembler), (void **)&asmr)) ||
      FAILED(DxcCreateInstance(CLSID_DxcValidator, __uuidof(IDxcValidator), (void **)&val))) {
    fprintf(stderr, "DxcCreateInstance failed\n");
    return 3;
  }
  fprintf(stderr, "instances created in %.1f ms\n", ms_since(t_load));
  const int runs = 5;
  for (int i = 1; i < argc; ++i) {
    std::string bytes = read_all(argv[i]);
    std::vector<double> dis, as, va;
    bool ok = true;
    for (int r = 0; r < runs && ok; ++r) {
      IDxcBlobEncoding *in = nullptr;
      utils->CreateBlob(bytes.data(), (UINT32)bytes.size(), DXC_CP_ACP, &in);
      auto t0 = Clock::now();
      IDxcBlobEncoding *text = nullptr;
      HRESULT hr = compiler->Disassemble(in, &text);
      dis.push_back(ms_since(t0));
      if (FAILED(hr) || !text) { ok = false; break; }
      IDxcBlobEncoding *src = nullptr;
      utils->CreateBlob(text->GetBufferPointer(), (UINT32)text->GetBufferSize(), DXC_CP_UTF8, &src);
      t0 = Clock::now();
      IDxcOperationResult *res = nullptr;
      hr = asmr->AssembleToContainer(src, &res);
      HRESULT st = E_FAIL;
      if (res) res->GetStatus(&st);
      as.push_back(ms_since(t0));
      if (FAILED(hr) || FAILED(st)) { ok = false; break; }
      IDxcBlob *out = nullptr;
      res->GetResult(&out);
      t0 = Clock::now();
      IDxcOperationResult *vr = nullptr;
      hr = val->Validate(out, DxcValidatorFlags_InPlaceEdit, &vr);
      HRESULT vs = E_FAIL;
      if (vr) vr->GetStatus(&vs);
      va.push_back(ms_since(t0));
      if (FAILED(hr) || FAILED(vs)) { ok = false; break; }
      vr->Release(); out->Release(); res->Release(); src->Release(); text->Release(); in->Release();
    }
    if (!ok) {
      printf("%s\tfailed\n", argv[i]);
      continue;
    }
    auto med = [](std::vector<double> v) { std::sort(v.begin(), v.end()); return v[v.size() / 2]; };
    auto mx = [](const std::vector<double> &v) { return *std::max_element(v.begin(), v.end()); };
    printf("%s\t%zu\tdisasm %.2f %.2f\tassemble %.2f %.2f\tvalidate %.2f %.2f\n", argv[i],
           bytes.size(), med(dis), mx(dis), med(as), mx(as), med(va), mx(va));
  }
  return 0;
}
