from pathlib import PurePosixPath
import unittest

from backend.file_policy import is_hidden_resource


class FilePolicyTests(unittest.TestCase):
    def test_documents_are_excluded_for_all_change_types(self):
        for suffix in ('md','MARKDOWN','rst','adoc','pdf','docx','pptx'):
            for status in ('A','M','D','T','C','R100'):
                with self.subTest(suffix=suffix,status=status):
                    self.assertTrue(is_hidden_resource({'status':status,'path':'docs/guide.'+suffix,'old_path':'old.md'}))
        for name in ('README','CHANGELOG.txt','LICENSE','docs/readme.MD'):
            self.assertTrue(is_hidden_resource({'status':'M','path':name}))
        for path in ('requirements.txt','CMakeLists.txt','docs/component.mdx','docs/example.py'):
            self.assertFalse(is_hidden_resource({'status':'M','path':path}))
        for old,path in [('old.py','new.md'),('old.md','new.py')]:
            self.assertFalse(is_hidden_resource({'status':'R100','path':path,'old_path':old}))

    def test_resource_extensions_are_hidden_for_add_modify_copy(self):
        extensions = (
            "png jpg jpeg gif webp svg ico avif bmp tif tiff heic "
            "woff woff2 ttf otf eot mp3 wav ogg flac aac m4a "
            "mp4 mov webm avi mkv glb gltf fbx psd sketch"
        ).split()
        for status in ("A", "M", "C", "A100", "M75", "C90"):
            for extension in extensions:
                with self.subTest(status=status, extension=extension):
                    self.assertTrue(is_hidden_resource({"status": status, "path": f"assets/icon.{extension.upper()}"}))

    def test_source_config_and_data_files_remain_visible_even_in_assets(self):
        for extension in ("js", "ts", "vue", "css", "json", "yaml", "yml", "xml", "mdx", "map", "obj", "bin"):
            with self.subTest(extension=extension):
                self.assertFalse(is_hidden_resource({"status": "M", "path": f"assets/public/file.{extension}"}))

    def test_reference_impact_statuses_remain_visible(self):
        for status in ("R", "R100", "D", "T", "R75"):
            with self.subTest(status=status):
                self.assertFalse(is_hidden_resource({"status": status, "path": "assets/icon.png"}))

    def test_missing_or_invalid_paths_are_visible(self):
        cases = (
            {},
            {"status": "A"},
            {"status": "A", "path": None},
            {"status": "A", "path": ""},
            {"status": "A", "path": "assets/\x00icon.png"},
            {"status": "A", "path": PurePosixPath("assets/icon.png")},
            None,
        )
        for file in cases:
            with self.subTest(file=file):
                self.assertFalse(is_hidden_resource(file))

    def test_case_and_directory_variants(self):
        self.assertTrue(is_hidden_resource({"status": "m", "path": "PUBLIC\u002ficons\u002fLogo.SvG"}))
        self.assertTrue(is_hidden_resource({"status": "A", "path": "design/logo.final.PSD"}))
        self.assertFalse(is_hidden_resource({"status": "A", "path": "assets/requirements.txt"}))


if __name__ == "__main__":
    unittest.main()
