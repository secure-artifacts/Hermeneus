// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AILiveInterpreterApp",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AILiveInterpreterApp", targets: ["AILiveInterpreterApp"])
    ],
    dependencies: [
        .package(path: "../Packages/AudioCore"),
        .package(path: "../Packages/VADKit"),
        .package(path: "../Packages/ASRKit"),
        .package(path: "../Packages/TranslateKit"),
        .package(path: "../Packages/TTSKit")
    ],
    targets: [
        .executableTarget(
            name: "AILiveInterpreterApp",
            dependencies: [
                .product(name: "AudioCore", package: "AudioCore"),
                .product(name: "VADKit", package: "VADKit"),
                .product(name: "ASRKit", package: "ASRKit"),
                .product(name: "TranslateKit", package: "TranslateKit"),
                .product(name: "TTSKit", package: "TTSKit")
            ],
            path: "Sources"
        )
    ]
)
