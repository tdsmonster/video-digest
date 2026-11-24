// vision-ocr: macOS Vision 框架文字识别（Apple Silicon 上走神经网络引擎，很快）
// 用法: vision-ocr <img1> [img2 ...]
// 输出: JSON 数组，与输入顺序一致；每项是该图识别出的文字（多行用 \n 连接，置信度 <0.5 的行丢弃）
// 首次使用时由 video_digest 自动用 swiftc 编译（需要 Xcode Command Line Tools）。
import Foundation
import Vision
import ImageIO

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US", "ja-JP", "ko-KR"]

func ocr(_ path: String) -> String {
    return autoreleasepool {
        guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
              let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return "" }
        let handler = VNImageRequestHandler(cgImage: img, options: [:])
        guard (try? handler.perform([request])) != nil, let results = request.results else { return "" }
        var lines: [String] = []
        for obs in results {
            guard let top = obs.topCandidates(1).first, top.confidence >= 0.5 else { continue }
            let t = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { lines.append(t) }
        }
        return lines.joined(separator: "\n")
    }
}

let paths = Array(CommandLine.arguments.dropFirst())
if paths.isEmpty {
    FileHandle.standardError.write("usage: vision-ocr <image> [image ...]\n".data(using: .utf8)!)
    exit(1)
}
let out = paths.map { ocr($0) }
let data = try! JSONSerialization.data(withJSONObject: out, options: [])
FileHandle.standardOutput.write(data)
