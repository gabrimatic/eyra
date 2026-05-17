// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "EyraMenuBar",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .library(name: "EyraMenuBarCore", targets: ["EyraMenuBarCore"]),
        .executable(name: "EyraMenuBar", targets: ["EyraMenuBar"])
    ],
    targets: [
        .target(
            name: "EyraMenuBarCore",
            path: "Sources/EyraMenuBarCore"
        ),
        .executableTarget(
            name: "EyraMenuBar",
            dependencies: ["EyraMenuBarCore"],
            path: "Sources/EyraMenuBar"
        ),
        .testTarget(
            name: "EyraMenuBarTests",
            dependencies: ["EyraMenuBarCore"],
            path: "Tests/EyraMenuBarTests"
        )
    ]
)
