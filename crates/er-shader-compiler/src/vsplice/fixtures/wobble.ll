target datalayout = "e-m:e-p:32:32-i1:32-i8:32-i16:32-i32:32-i64:64-f16:32-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-dx"

%dx.types.Handle = type { i8* }
%dx.types.CBufRet.f32 = type { float, float, float, float }
%EditorParams = type { [1024 x <4 x float>] }

define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 0, i32 0, i1 false)
  %2 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 0, i32 undef)
  %3 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)
  %4 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 1, i32 undef)
  %5 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 2, i32 undef)
  %6 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 257)
  %7 = extractvalue %dx.types.CBufRet.f32 %6, 0
  %8 = fmul fast float %4, %7
  %9 = fadd fast float %8, %2
  %10 = call float @dx.op.unary.f32(i32 13, float %9)
  %11 = fmul fast float %10, 0x3FA99999A0000000
  %12 = fadd fast float %11, %3
  %13 = fadd fast float %11, %5
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %12)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %4)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 2, float %13)
  ret void
}

; Function Attrs: nounwind readnone
declare float @dx.op.loadInput.f32(i32, i32, i32, i8, i32) #0

; Function Attrs: nounwind
declare void @dx.op.storeOutput.f32(i32, i32, i32, i8, float) #1

; Function Attrs: nounwind readnone
declare float @dx.op.unary.f32(i32, float) #0

; Function Attrs: nounwind readonly
declare %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32, %dx.types.Handle, i32) #2

; Function Attrs: nounwind readonly
declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1) #2

attributes #0 = { nounwind readnone }
attributes #1 = { nounwind }
attributes #2 = { nounwind readonly }

!dx.version = !{!1}
!dx.valver = !{!2}
!dx.shaderModel = !{!3}
!dx.resources = !{!4}
!dx.entryPoints = !{!8}

!1 = !{i32 1, i32 0}
!2 = !{i32 1, i32 6}
!3 = !{!"vs", i32 6, i32 0}
!4 = !{null, null, !5, null}
!5 = !{!6}
!6 = !{i32 0, %EditorParams* undef, !"", i32 42, i32 0, i32 1, i32 16384, null}
!8 = !{void ()* @main, !"main", !9, !4, null}
!9 = !{!10, !16, null}
!10 = !{!11, !14}
!11 = !{i32 0, !"ER_POS", i8 9, i8 0, !12, i8 0, i32 1, i8 3, i32 0, i8 0, !13}
!12 = !{i32 0}
!13 = !{i32 3, i32 7}
!14 = !{i32 1, !"ER_TIME", i8 9, i8 0, !12, i8 0, i32 1, i8 1, i32 1, i8 0, !15}
!15 = !{i32 3, i32 1}
!16 = !{!17}
!17 = !{i32 0, !"ER_OUT", i8 9, i8 0, !12, i8 2, i32 1, i8 3, i32 0, i8 0, !13}
