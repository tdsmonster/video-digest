import Foundation
import Vision
import CoreGraphics
import CoreText
import ImageIO

// 统一输出数据结构
struct FrameEnhanceResult: Codable {
    let index: Int
    let ocrText: String?
    let saliencyFocusX: Double?
    let saliencyFocusY: Double?
    let error: String?
}

struct BatchRequest: Codable {
    let frames: [FrameTask]
    let enableOcr: Bool
    let enableSaliency: Bool
    let ocrMode: String // "fast" or "accurate"
}

struct FrameTask: Codable {
    let index: Int
    let path: String
}

class VisionEngine {
    static let shared = VisionEngine()
    
    private var textRequestAccurate: VNRecognizeTextRequest?
    private var textRequestFast: VNRecognizeTextRequest?
    private var saliencyRequest: VNGenerateAttentionBasedSaliencyImageRequest?
    
    init() {
        let reqAcc = VNRecognizeTextRequest()
        reqAcc.recognitionLevel = .accurate
        reqAcc.usesLanguageCorrection = true
        reqAcc.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US", "ja-JP", "ko-KR"]
        self.textRequestAccurate = reqAcc
        
        let reqFast = VNRecognizeTextRequest()
        reqFast.recognitionLevel = .fast
        reqFast.usesLanguageCorrection = false
        reqFast.recognitionLanguages = ["zh-Hans", "en-US"]
        self.textRequestFast = reqFast
        
        self.saliencyRequest = VNGenerateAttentionBasedSaliencyImageRequest()
    }
    
    func process(task: FrameTask, enableOcr: Bool, enableSaliency: Bool, ocrMode: String) -> FrameEnhanceResult {
        return autoreleasepool { () -> FrameEnhanceResult in
            let url = URL(fileURLWithPath: task.path)
            guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil),
                  let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
                return FrameEnhanceResult(index: task.index, ocrText: nil, saliencyFocusX: nil, saliencyFocusY: nil, error: "Failed to load image")
            }
            
            var requests = [VNRequest]()
            let ocrReq = (ocrMode == "fast") ? textRequestFast : textRequestAccurate
            if enableOcr, let req = ocrReq {
                requests.append(req)
            }
            if enableSaliency, let req = saliencyRequest {
                requests.append(req)
            }
            
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform(requests)
            } catch {
                return FrameEnhanceResult(index: task.index, ocrText: nil, saliencyFocusX: nil, saliencyFocusY: nil, error: error.localizedDescription)
            }
            
            var ocrOutput: String? = nil
            if enableOcr, let results = ocrReq?.results {
                var lines = [String]()
                for observation in results {
                    guard let topCandidate = observation.topCandidates(1).first else { continue }
                    let conf = topCandidate.confidence
                    let text = topCandidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { continue }
                    
                    if conf < 0.6 {
                        lines.append("[low-conf] \(text)")
                    } else {
                        lines.append(text)
                    }
                }
                if !lines.isEmpty {
                    ocrOutput = lines.joined(separator: "\n")
                }
            }
            
            var focusX: Double? = nil
            var focusY: Double? = nil
            if enableSaliency, let salResults = saliencyRequest?.results?.first {
                if let objects = salResults.salientObjects, !objects.isEmpty {
                    let sortedObjects = objects.sorted { $0.confidence > $1.confidence }
                    if let top1 = sortedObjects.first, top1.confidence >= 0.3 {
                        let bb = top1.boundingBox
                        let centerX = Double(bb.midX)
                        let centerY_vision = Double(bb.midY)
                        let centerY_video = 1.0 - centerY_vision
                        focusX = centerX
                        focusY = centerY_video
                    }
                }
            }
            
            return FrameEnhanceResult(index: task.index, ocrText: ocrOutput, saliencyFocusX: focusX, saliencyFocusY: focusY, error: nil)
        }
    }
}

func main() {
    let args = CommandLine.arguments
    guard args.count >= 2 else {
        fputs("Usage: mac-vision <task.json>\n", stderr)
        exit(1)
    }
    
    let taskPath = args[1]
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: taskPath)) else {
        fputs("Error: cannot read task file: \(taskPath)\n", stderr)
        exit(2)
    }
    
    guard let batch = try? JSONDecoder().decode(BatchRequest.self, from: data) else {
        fputs("Error: invalid JSON in task file\n", stderr)
        exit(3)
    }
    
    var results = [FrameEnhanceResult]()
    for frame in batch.frames {
        let res = VisionEngine.shared.process(task: frame, enableOcr: batch.enableOcr, enableSaliency: batch.enableSaliency, ocrMode: batch.ocrMode)
        results.append(res)
    }
    
    let encoder = JSONEncoder()
    encoder.outputFormatting = .prettyPrinted
    if let outData = try? encoder.encode(results), let outStr = String(data: outData, encoding: .utf8) {
        print(outStr)
    } else {
        fputs("Error: failed to encode results\n", stderr)
        exit(4)
    }
}

main()
