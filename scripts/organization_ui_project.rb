# Generate an isolated XCTest runner for the dedicated UI fixture simulator.
# Requires xcodeproj (also distributed with CocoaPods). No app source is copied.
require 'xcodeproj'
require 'fileutils'
dir = File.expand_path(ARGV.fetch(0))
FileUtils.mkdir_p(dir)
p = Xcodeproj::Project.new(File.join(dir, 'OrganizationUI.xcodeproj'))
t = p.new_target(:ui_test_bundle, 'OrganizationUITests', :ios, '15.1')
source = File.expand_path('../app/tests/OrganizationUITests.swift', __dir__)
t.add_file_references([p.main_group.new_file(source)])
t.build_configurations.each do |c|
  c.build_settings.merge!({
    'SWIFT_VERSION' => '5.0', 'PRODUCT_BUNDLE_IDENTIFIER' => 'com.reelbot.organizationuitests',
    'GENERATE_INFOPLIST_FILE' => 'YES', 'CODE_SIGNING_ALLOWED' => 'NO',
    'TARGETED_DEVICE_FAMILY' => '1', 'SUPPORTED_PLATFORMS' => 'iphonesimulator',
    'IPHONEOS_DEPLOYMENT_TARGET' => '15.1'
  })
end
p.save
s = Xcodeproj::XCScheme.new
s.add_build_target(t)
s.add_test_target(t)
s.save_as(p.path, 'OrganizationUITests', true)
