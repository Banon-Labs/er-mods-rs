//! Boot/loading-screen frame rasterizer + save-picker overlay host (product side).
//!
//! The portrait capture pipeline (staged color+depth readback, depth-key worker,
//! portrait/stats CPU compositors, frame bridge) moved to the `er-loading-portrait-core`
//! crate (portrait crate split). A `pub(crate) use er_loading_portrait_core::*` shim used to sit here
//! so every remaining flat-namespace reference (BootViewFrame, portrait_onto, RGBA8_BPP,
//! MAX_RT_DIM, OVERLAY_FENCE_VAL, record_transition, ...) kept compiling unchanged. Those
//! references are gone -- the 2026-08-21 lint-parity sweep pruned the last of them -- so the shim
//! resolved nothing and rustc 1.98 flagged it. What this module still needs it names directly.

use super::*;

// The shared import block for the remaining modules below (it used to live at the
// top of resource_readback.rs before that file moved to er-loading-portrait-core).
use std::mem::ManuallyDrop;

use windows::Win32::Foundation::GENERIC_READ;
use windows::Win32::Graphics::Direct3D::D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST;
use windows::Win32::Graphics::Direct3D12::{
    D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_COMMAND_QUEUE_DESC, D3D12_COMMAND_QUEUE_FLAG_NONE,
    D3D12_FENCE_FLAG_NONE, D3D12_PLACED_SUBRESOURCE_FOOTPRINT, D3D12_RESOURCE_STATE_COPY_DEST,
    D3D12_RESOURCE_STATE_PRESENT, D3D12_TEXTURE_COPY_LOCATION, D3D12_TEXTURE_COPY_LOCATION_0,
    D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT, D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,
    ID3D12CommandAllocator, ID3D12CommandQueue, ID3D12DescriptorHeap, ID3D12Device, ID3D12Fence,
    ID3D12GraphicsCommandList, ID3D12PipelineState, ID3D12Resource, ID3D12RootSignature,
};
use windows::Win32::Graphics::Direct3D12::{
    D3D12_DESCRIPTOR_HEAP_DESC, D3D12_DESCRIPTOR_HEAP_FLAG_NONE,
    D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE, D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV,
    D3D12_DESCRIPTOR_HEAP_TYPE_RTV, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE,
    D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_VIEWPORT,
};
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT;
use windows::Win32::Graphics::Dxgi::IDXGISwapChain3;
use windows::Win32::Graphics::Imaging::{
    CLSID_WICImagingFactory, GUID_WICPixelFormat32bppRGBA, IWICBitmapSource, IWICImagingFactory,
    WICConvertBitmapSource, WICDecodeMetadataCacheOnDemand,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx,
};
use windows::core::{IUnknown, Interface, PCWSTR};

// The shared D3D12 draw plumbing (HLSL compile, root-signature/PSO builders, the texture+upload+SRV
// slot creator, SRV handle math, the execute/fence-wait submit) moved to
// `er_loading_portrait_core::gpu_draw_shared` with the loading-cover crate extraction. Private glob
// on purpose, exactly as the local module's was: nothing here is `pub(crate)`, so a `pub(crate) use`
// would re-export nothing and rustc would report it as an unused import. Children still reach these
// through their own `use super::*`.
use er_loading_portrait_core::gpu_draw_shared::*;

mod boot_progress;
pub(crate) use boot_progress::*;

mod save_picker_overlay;
pub(crate) use save_picker_overlay::*;
