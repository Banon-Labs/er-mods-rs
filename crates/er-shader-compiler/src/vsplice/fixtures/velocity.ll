target datalayout = "e-m:e-p:32:32-i1:32-i8:32-i16:32-i32:32-i64:64-f16:32-f32:32-f64:64-n8:16:32:64"
target triple = "dxil-ms-dx"

%dx.types.Handle = type { i8* }
%dx.types.CBufRet.f32 = type { float, float, float, float }
%dx.types.CBufRet.i32 = type { i32, i32, i32, i32 }
%hostlayout.cbSceneParam = type { [24 x <4 x float>], [4 x <4 x float>], <4 x float>, [4 x <4 x float>] }
%cbObjMatrix = type { <4 x float>, i32 }

define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 1, i32 5, i1 false)  ; CreateHandle(resourceClass,rangeId,index,nonUniformIndex)
  %2 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 0, i32 8, i1 false)  ; CreateHandle(resourceClass,rangeId,index,nonUniformIndex)
  %3 = call i32 @dx.op.loadInput.i32(i32 4, i32 2, i32 0, i8 0, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %4 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 0, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %5 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 1, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %6 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 2, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %7 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %8 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 1, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %9 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 2, i32 undef)  ; LoadInput(inputSigId,rowIndex,colIndex,gsVertexAxis)
  %10 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %1, i32 0)  ; CBufferLoadLegacy(handle,regIndex)
  %11 = extractvalue %dx.types.CBufRet.f32 %10, 0
  %12 = extractvalue %dx.types.CBufRet.f32 %10, 1
  %13 = extractvalue %dx.types.CBufRet.f32 %10, 2
  %14 = fadd fast float %11, %7
  %15 = fadd fast float %12, %8
  %16 = fadd fast float %13, %9
  %17 = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %1, i32 1)  ; CBufferLoadLegacy(handle,regIndex)
  %18 = extractvalue %dx.types.CBufRet.i32 %17, 0
  %19 = icmp ult i32 %3, %18
  br i1 %19, label %20, label %25

; <label>:20                                      ; preds = %0
  %21 = extractvalue %dx.types.CBufRet.f32 %10, 3
  %22 = uitofp i32 %3 to float
  %23 = fmul fast float %21, %22
  %24 = fadd fast float %23, %15
  br label %25

; <label>:25                                      ; preds = %20, %0
  %26 = phi float [ %24, %20 ], [ %15, %0 ]
  %27 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 24)  ; CBufferLoadLegacy(handle,regIndex)
  %28 = extractvalue %dx.types.CBufRet.f32 %27, 0
  %29 = extractvalue %dx.types.CBufRet.f32 %27, 1
  %30 = extractvalue %dx.types.CBufRet.f32 %27, 2
  %31 = extractvalue %dx.types.CBufRet.f32 %27, 3
  %32 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 25)  ; CBufferLoadLegacy(handle,regIndex)
  %33 = extractvalue %dx.types.CBufRet.f32 %32, 0
  %34 = extractvalue %dx.types.CBufRet.f32 %32, 1
  %35 = extractvalue %dx.types.CBufRet.f32 %32, 2
  %36 = extractvalue %dx.types.CBufRet.f32 %32, 3
  %37 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 26)  ; CBufferLoadLegacy(handle,regIndex)
  %38 = extractvalue %dx.types.CBufRet.f32 %37, 0
  %39 = extractvalue %dx.types.CBufRet.f32 %37, 1
  %40 = extractvalue %dx.types.CBufRet.f32 %37, 2
  %41 = extractvalue %dx.types.CBufRet.f32 %37, 3
  %42 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 27)  ; CBufferLoadLegacy(handle,regIndex)
  %43 = extractvalue %dx.types.CBufRet.f32 %42, 0
  %44 = extractvalue %dx.types.CBufRet.f32 %42, 1
  %45 = extractvalue %dx.types.CBufRet.f32 %42, 2
  %46 = extractvalue %dx.types.CBufRet.f32 %42, 3
  %47 = fmul fast float %28, %14
  %48 = call float @dx.op.tertiary.f32(i32 46, float %26, float %29, float %47)  ; FMad(a,b,c)
  %49 = call float @dx.op.tertiary.f32(i32 46, float %16, float %30, float %48)  ; FMad(a,b,c)
  %50 = fadd fast float %49, %31
  %51 = fmul fast float %33, %14
  %52 = call float @dx.op.tertiary.f32(i32 46, float %26, float %34, float %51)  ; FMad(a,b,c)
  %53 = call float @dx.op.tertiary.f32(i32 46, float %16, float %35, float %52)  ; FMad(a,b,c)
  %54 = fadd fast float %53, %36
  %55 = fmul fast float %38, %14
  %56 = call float @dx.op.tertiary.f32(i32 46, float %26, float %39, float %55)  ; FMad(a,b,c)
  %57 = call float @dx.op.tertiary.f32(i32 46, float %16, float %40, float %56)  ; FMad(a,b,c)
  %58 = fadd fast float %57, %41
  %59 = fmul fast float %43, %14
  %60 = call float @dx.op.tertiary.f32(i32 46, float %26, float %44, float %59)  ; FMad(a,b,c)
  %61 = call float @dx.op.tertiary.f32(i32 46, float %16, float %45, float %60)  ; FMad(a,b,c)
  %62 = fadd fast float %61, %46
  %63 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 29)  ; CBufferLoadLegacy(handle,regIndex)
  %64 = extractvalue %dx.types.CBufRet.f32 %63, 0
  %65 = extractvalue %dx.types.CBufRet.f32 %63, 1
  %66 = extractvalue %dx.types.CBufRet.f32 %63, 2
  %67 = extractvalue %dx.types.CBufRet.f32 %63, 3
  %68 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 30)  ; CBufferLoadLegacy(handle,regIndex)
  %69 = extractvalue %dx.types.CBufRet.f32 %68, 0
  %70 = extractvalue %dx.types.CBufRet.f32 %68, 1
  %71 = extractvalue %dx.types.CBufRet.f32 %68, 2
  %72 = extractvalue %dx.types.CBufRet.f32 %68, 3
  %73 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 31)  ; CBufferLoadLegacy(handle,regIndex)
  %74 = extractvalue %dx.types.CBufRet.f32 %73, 0
  %75 = extractvalue %dx.types.CBufRet.f32 %73, 1
  %76 = extractvalue %dx.types.CBufRet.f32 %73, 2
  %77 = extractvalue %dx.types.CBufRet.f32 %73, 3
  %78 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 32)  ; CBufferLoadLegacy(handle,regIndex)
  %79 = extractvalue %dx.types.CBufRet.f32 %78, 0
  %80 = extractvalue %dx.types.CBufRet.f32 %78, 1
  %81 = extractvalue %dx.types.CBufRet.f32 %78, 2
  %82 = extractvalue %dx.types.CBufRet.f32 %78, 3
  %83 = fmul fast float %64, %14
  %84 = call float @dx.op.tertiary.f32(i32 46, float %26, float %65, float %83)  ; FMad(a,b,c)
  %85 = call float @dx.op.tertiary.f32(i32 46, float %16, float %66, float %84)  ; FMad(a,b,c)
  %86 = fadd fast float %85, %67
  %87 = fmul fast float %69, %14
  %88 = call float @dx.op.tertiary.f32(i32 46, float %26, float %70, float %87)  ; FMad(a,b,c)
  %89 = call float @dx.op.tertiary.f32(i32 46, float %16, float %71, float %88)  ; FMad(a,b,c)
  %90 = fadd fast float %89, %72
  %91 = fmul fast float %74, %14
  %92 = call float @dx.op.tertiary.f32(i32 46, float %26, float %75, float %91)  ; FMad(a,b,c)
  %93 = call float @dx.op.tertiary.f32(i32 46, float %16, float %76, float %92)  ; FMad(a,b,c)
  %94 = fadd fast float %93, %77
  %95 = fmul fast float %79, %14
  %96 = call float @dx.op.tertiary.f32(i32 46, float %26, float %80, float %95)  ; FMad(a,b,c)
  %97 = call float @dx.op.tertiary.f32(i32 46, float %16, float %81, float %96)  ; FMad(a,b,c)
  %98 = fadd fast float %97, %82
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %50)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %54)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 2, float %58)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 3, float %62)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 1, i32 0, i8 0, float %86)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 1, i32 0, i8 1, float %90)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 1, i32 0, i8 2, float %94)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 1, i32 0, i8 3, float %98)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 2, i32 0, i8 0, float %4)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 2, i32 0, i8 1, float %5)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  call void @dx.op.storeOutput.f32(i32 5, i32 2, i32 0, i8 2, float %6)  ; StoreOutput(outputSigId,rowIndex,colIndex,value)
  ret void
}

; Function Attrs: nounwind readnone
declare float @dx.op.loadInput.f32(i32, i32, i32, i8, i32) #0

; Function Attrs: nounwind readnone
declare i32 @dx.op.loadInput.i32(i32, i32, i32, i8, i32) #0

; Function Attrs: nounwind
declare void @dx.op.storeOutput.f32(i32, i32, i32, i8, float) #1

; Function Attrs: nounwind readonly
declare %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32, %dx.types.Handle, i32) #2

; Function Attrs: nounwind readonly
declare %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32, %dx.types.Handle, i32) #2

; Function Attrs: nounwind readnone
declare float @dx.op.tertiary.f32(i32, float, float, float) #0

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
!dx.viewIdState = !{!8}
!dx.entryPoints = !{!9}

!0 = !{!"dxc(private) 1.9.0.5191 (d355aa836)"}
!1 = !{i32 1, i32 0}
!2 = !{i32 1, i32 6}
!3 = !{!"vs", i32 6, i32 0}
!4 = !{null, null, !5, null}
!5 = !{!6, !7}
!6 = !{i32 0, %hostlayout.cbSceneParam* undef, !"", i32 0, i32 8, i32 1, i32 528, null}
!7 = !{i32 1, %cbObjMatrix* undef, !"", i32 0, i32 5, i32 1, i32 20, null}
!8 = !{[11 x i32] [i32 9, i32 11, i32 255, i32 255, i32 255, i32 0, i32 256, i32 512, i32 1024, i32 0, i32 255]}
!9 = !{void ()* @main, !"main", !10, !4, null}
!10 = !{!11, !18, null}
!11 = !{!12, !15, !16}
!12 = !{i32 0, !"POSITION", i8 9, i8 0, !13, i8 0, i32 1, i8 3, i32 0, i8 0, !14}
!13 = !{i32 0}
!14 = !{i32 3, i32 7}
!15 = !{i32 1, !"NORMAL", i8 9, i8 0, !13, i8 0, i32 1, i8 3, i32 1, i8 0, !14}
!16 = !{i32 2, !"SV_InstanceID", i8 5, i8 2, !13, i8 0, i32 1, i8 1, i32 2, i8 0, !17}
!17 = !{i32 3, i32 1}
!18 = !{!19, !21, !23}
!19 = !{i32 0, !"SV_Position", i8 9, i8 3, !13, i8 4, i32 1, i8 4, i32 0, i8 0, !20}
!20 = !{i32 3, i32 15}
!21 = !{i32 1, !"POSITION", i8 9, i8 0, !22, i8 2, i32 1, i8 4, i32 1, i8 0, !20}
!22 = !{i32 1}
!23 = !{i32 2, !"NORMAL", i8 9, i8 0, !13, i8 2, i32 1, i8 3, i32 2, i8 0, !14}
