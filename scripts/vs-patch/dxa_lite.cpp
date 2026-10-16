// Assemble DXIL LLVM IR text (or bitcode) into a DXIL container with
// libdxcompiler's IDxcAssembler, then validate and sign it in place with the
// internal IDxcValidator. Usage: dxa_lite <in.ll> <out.dxil> [--no-validate]
//
// `dxa_lite --serve` keeps libdxcompiler loaded and answers one request per
// line of stdin, so a sweep pays for the library once and the time of each
// step is measured inside this process, around the call alone -- what the
// shader editor's helper, which also holds the library, would spend. Fields
// are tab-separated, and every answer is one line:
//
//   dis <in.dxil> <out.ll>   IDxcCompiler::Disassemble, the text `dxc -dumpbin`
//                            prints: `ok <ms>` or `err dis <message>`
//   asm <in.ll> <out.dxil>   assemble, then validate in place (which signs):
//                            `ok <assemble ms> <validate ms>`, or
//                            `err asm|validate <message>`; a container that
//                            failed validation is written to <out>.unvalidated
//
// Built against the DXC 1.9 Linux release this repo uses:
//   g++ -std=c++17 -O2 -I ~/tools/dxc/include scripts/vs-patch/dxa_lite.cpp \
//       -L ~/tools/dxc/lib -ldxcompiler -Wl,-rpath,$HOME/tools/dxc/lib -o dxa_lite
#include <dxc/WinAdapter.h>
#include <dxc/dxcapi.h>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

static std::string read_all(const std::string &p) {
  std::ifstream f(p, std::ios::binary);
  std::stringstream s;
  s << f.rdbuf();
  return s.str();
}

static void write_all(const std::string &p, const void *data, size_t size) {
  std::ofstream o(p, std::ios::binary);
  o.write((const char *)data, size);
}

static std::string errors_of(IDxcOperationResult *r) {
  IDxcBlobEncoding *e = nullptr;
  std::string out;
  if (r && SUCCEEDED(r->GetErrorBuffer(&e)) && e && e->GetBufferSize()) {
    out.assign((const char *)e->GetBufferPointer(), e->GetBufferSize());
    e->Release();
  }
  return out;
}

// One line: tabs and line breaks become spaces, and NULs are dropped.
static std::string one_line(std::string text) {
  std::string out;
  for (char c : text) {
    if (c == '\n' || c == '\r' || c == '\t') {
      out.push_back(' ');
    } else if (c != '\0') {
      out.push_back(c);
    }
  }
  return out;
}

using Clock = std::chrono::steady_clock;

static double ms_since(Clock::time_point t0) {
  return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

struct Dxc {
  IDxcUtils *utils = nullptr;
  IDxcCompiler *compiler = nullptr;
  IDxcAssembler *assembler = nullptr;
  IDxcValidator *validator = nullptr;

  bool create() {
    return SUCCEEDED(DxcCreateInstance(CLSID_DxcUtils, __uuidof(IDxcUtils), (void **)&utils)) &&
           SUCCEEDED(DxcCreateInstance(CLSID_DxcCompiler, __uuidof(IDxcCompiler),
                                       (void **)&compiler)) &&
           SUCCEEDED(DxcCreateInstance(CLSID_DxcAssembler, __uuidof(IDxcAssembler),
                                       (void **)&assembler)) &&
           SUCCEEDED(DxcCreateInstance(CLSID_DxcValidator, __uuidof(IDxcValidator),
                                       (void **)&validator));
  }

  std::string disassemble(const std::string &in, const std::string &out) {
    std::string bytes = read_all(in);
    IDxcBlobEncoding *blob = nullptr;
    utils->CreateBlob(bytes.data(), (UINT32)bytes.size(), DXC_CP_ACP, &blob);
    auto t0 = Clock::now();
    IDxcBlobEncoding *text = nullptr;
    HRESULT hr = compiler->Disassemble(blob, &text);
    double ms = ms_since(t0);
    blob->Release();
    if (FAILED(hr) || !text) {
      char buffer[64];
      snprintf(buffer, sizeof buffer, "err\tdis\thr=%08x", (unsigned)hr);
      return buffer;
    }
    // The text blob counts its terminating NUL in some builds.
    size_t size = text->GetBufferSize();
    const char *data = (const char *)text->GetBufferPointer();
    while (size > 0 && data[size - 1] == '\0') {
      --size;
    }
    write_all(out, data, size);
    text->Release();
    char buffer[64];
    snprintf(buffer, sizeof buffer, "ok\t%.4f", ms);
    return buffer;
  }

  // Assemble `in`, validate in place when asked, and write `out`; the answer line.
  std::string assemble(const std::string &in, const std::string &out, bool validate) {
    std::string src = read_all(in);
    IDxcBlobEncoding *blob = nullptr;
    utils->CreateBlob(src.data(), (UINT32)src.size(), DXC_CP_UTF8, &blob);
    auto t0 = Clock::now();
    IDxcOperationResult *res = nullptr;
    HRESULT hr = assembler->AssembleToContainer(blob, &res);
    HRESULT st = E_FAIL;
    if (res) res->GetStatus(&st);
    double assemble_ms = ms_since(t0);
    blob->Release();
    if (FAILED(hr) || FAILED(st)) {
      std::string why = one_line(errors_of(res));
      if (res) res->Release();
      char buffer[64];
      snprintf(buffer, sizeof buffer, "err\tasm\thr=%08x status=%08x ", (unsigned)hr,
               (unsigned)st);
      return buffer + why;
    }
    IDxcBlob *container = nullptr;
    res->GetResult(&container);
    res->Release();
    double validate_ms = 0.0;
    if (validate) {
      t0 = Clock::now();
      IDxcOperationResult *vr = nullptr;
      hr = validator->Validate(container, DxcValidatorFlags_InPlaceEdit, &vr);
      HRESULT vs = E_FAIL;
      if (vr) vr->GetStatus(&vs);
      validate_ms = ms_since(t0);
      if (FAILED(hr) || FAILED(vs)) {
        std::string why = one_line(errors_of(vr));
        if (vr) vr->Release();
        write_all(out + ".unvalidated", container->GetBufferPointer(),
                  container->GetBufferSize());
        container->Release();
        return "err\tvalidate\t" + why;
      }
      vr->Release();
    }
    write_all(out, container->GetBufferPointer(), container->GetBufferSize());
    container->Release();
    char buffer[96];
    snprintf(buffer, sizeof buffer, "ok\t%.4f\t%.4f", assemble_ms, validate_ms);
    return buffer;
  }
};

static int serve(Dxc &dxc) {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::vector<std::string> fields;
    std::stringstream s(line);
    std::string field;
    while (std::getline(s, field, '\t')) {
      fields.push_back(field);
    }
    std::string answer;
    if (fields.size() == 3 && fields[0] == "dis") {
      answer = dxc.disassemble(fields[1], fields[2]);
    } else if (fields.size() == 3 && fields[0] == "asm") {
      answer = dxc.assemble(fields[1], fields[2], true);
    } else {
      answer = "err\trequest\t" + one_line(line);
    }
    std::cout << answer << '\n' << std::flush;
  }
  return 0;
}

int main(int argc, char **argv) {
  Dxc dxc;
  if (!dxc.create()) {
    fprintf(stderr, "DxcCreateInstance failed\n");
    return 3;
  }
  if (argc == 2 && std::string(argv[1]) == "--serve") {
    return serve(dxc);
  }
  if (argc < 3) {
    fprintf(stderr, "usage: dxa_lite in.ll out.dxil [--no-validate] | dxa_lite --serve\n");
    return 2;
  }
  bool validate = !(argc > 3 && std::string(argv[3]) == "--no-validate");
  std::string answer = dxc.assemble(argv[1], argv[2], validate);
  if (answer.rfind("ok", 0) == 0) {
    return 0;
  }
  fprintf(stderr, "%s\n", answer.c_str());
  return answer.rfind("err\tasm", 0) == 0 ? 4 : 5;
}
