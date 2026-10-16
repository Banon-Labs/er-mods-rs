target datalayout = "e-m:e-p:32:32-i1:32-i8:32-i16:32-i32:32-i64:64-f16:32-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-dx"

%dx.types.Handle = type { i8* }
%dx.types.CBufRet.f32 = type { float, float, float, float }
%EditorParams = type { [1024 x <4 x float>] }

define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 0, i32 0, i1 false)  ; CreateHandle(resourceClass,rangeId,index,nonUniformIndex)
  %2 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 0, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %3 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %4 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 1, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %5 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 2, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %6 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 304)  ; CBufferLoadLegacy(handle,regIndex)
  %7 = extractvalue %dx.types.CBufRet.f32 %6, 0
  %8 = extractvalue %dx.types.CBufRet.f32 %6, 1
  %9 = fmul fast float %7, %8
  %10 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 305)  ; CBufferLoadLegacy(handle,regIndex)
  %11 = extractvalue %dx.types.CBufRet.f32 %10, 0
  %12 = extractvalue %dx.types.CBufRet.f32 %10, 1
  %13 = fmul fast float %12, %4
  %14 = extractvalue %dx.types.CBufRet.f32 %10, 2
  %15 = fmul fast float %14, %2
  %16 = fadd fast float %15, %13
  %17 = call float @dx.op.unary.f32(i32 13, float %16)  ; Sin(value)
  %18 = fmul fast float %17, %11
  %19 = fadd fast float %18, %3
  %20 = fmul fast float %19, %12
  %21 = fadd fast float %20, %15
  %22 = call float @dx.op.unary.f32(i32 12, float %21)  ; Cos(value)
  %23 = fmul fast float %18, %9
  %24 = fmul fast float %11, %9
  %25 = fmul fast float %24, %22
  %26 = fadd fast float %23, %3
  %27 = fadd fast float %25, %5
  %28 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 320)  ; CBufferLoadLegacy(handle,regIndex)
  %29 = extractvalue %dx.types.CBufRet.f32 %28, 0
  %30 = extractvalue %dx.types.CBufRet.f32 %28, 1
  %31 = fmul fast float %29, %30
  %32 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 321)  ; CBufferLoadLegacy(handle,regIndex)
  %33 = extractvalue %dx.types.CBufRet.f32 %32, 0
  %34 = fcmp fast olt float %4, %33
  br i1 %34, label %35, label %39

; <label>:35                                      ; preds = %0
  %36 = call float @dx.op.unary.f32(i32 13, float %2)  ; Sin(value)
  %37 = fmul fast float %36, 0x3FB99999A0000000
  %38 = fadd fast float %37, %33
  br label %39

; <label>:39                                      ; preds = %35, %0
  %40 = phi float [ %38, %35 ], [ %4, %0 ]
  %41 = fsub fast float %40, %4
  %42 = fmul fast float %31, %41
  %43 = fadd fast float %42, %4
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %26)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %43)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 2, float %27)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
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

!llvm.ident = !{!0}
!dx.version = !{!1}
!dx.valver = !{!2}
!dx.shaderModel = !{!3}
!dx.resources = !{!4}
!dx.viewIdState = !{!7}
!dx.entryPoints = !{!8}

!0 = !{!"dxc(private) 1.9.0.5191 (d355aa836)"}
!1 = !{i32 1, i32 0}
!2 = !{i32 1, i32 6}
!3 = !{!"vs", i32 6, i32 0}
!4 = !{null, null, !5, null}
!5 = !{!6}
!6 = !{i32 0, %EditorParams* undef, !"", i32 42, i32 0, i32 1, i32 16384, null}
!7 = !{[7 x i32] [i32 5, i32 3, i32 5, i32 7, i32 4, i32 0, i32 7]}
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
