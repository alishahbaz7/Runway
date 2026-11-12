// Runway launcher — the one program people pin. It never carries a version and
// rarely changes: it starts the newest complete version under .\versions and exits. The app itself
// checks for updates, downloads them into .\versions and restarts through this launcher.
//
//   <root>\Runway.exe                                   this program
//   <root>\versions\Runway-v9.0.0\Runway-v9.0.0.exe
//   <root>\versions\Runway-v9.1.0\Runway-v9.1.0.exe
//
// Hold Shift while starting it (or pass --choose) to pick an installed version — the way back if a
// new version misbehaves. Folders still being written (*.partial) are never started.
//
// Built with the C# compiler that ships with Windows (.NET Framework 4.x), so it needs nothing
// installed and runs on any Windows 10/11: see build.ps1. C# 5 syntax only.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

[assembly: AssemblyTitle("Runway")]
[assembly: AssemblyProduct("Runway")]
[assembly: AssemblyDescription("Starts the newest installed version of Runway")]

namespace RunwayLauncher
{
    internal sealed class Installed
    {
        public Version Version;
        public string Dir;
        public string Exe;
    }

    internal static class Program
    {
        private const string AppName = "Runway";
        private const string Title = "Runway";
        private static readonly Regex FolderPattern = new Regex("^" + Regex.Escape(AppName) + @"-v(\d+)\.(\d+)\.(\d+)$");
        private static string _root;

        [STAThread]
        private static int Main(string[] args)
        {
            _root = AppDomain.CurrentDomain.BaseDirectory;
            var versionsDir = Path.Combine(_root, "versions");
            var installed = Find(versionsDir);
            if (installed.Count == 0)
            {
                Log("no version installed in " + versionsDir);
                MessageBox.Show("No version of " + Title + " is installed in\n" + versionsDir,
                    Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 1;
            }

            var choose = (Control.ModifierKeys & Keys.Shift) == Keys.Shift || args.Contains("--choose");
            var passThrough = args.Where(a => a != "--choose" && a != "--after-update").ToArray();

            List<Installed> order = installed;
            if (choose)
            {
                var picked = Choose(installed);
                if (picked == null) return 0;
                order = new List<Installed> { picked };
            }

            foreach (var v in order)
            {
                try
                {
                    Start(v, passThrough);
                    Log("started " + v.Version + (args.Contains("--after-update") ? " after update" : ""));
                    return 0;
                }
                catch (Exception e)
                {
                    Log("could not start " + v.Version + ": " + e.GetType().Name + " " + e.Message);
                }
            }

            MessageBox.Show(Title + " could not start.\nDetails: " + Path.Combine(_root, "launcher.log"),
                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }

        /// <summary>Complete versions (folder name right, its program present), newest first.</summary>
        internal static List<Installed> Find(string versionsDir)
        {
            var list = new List<Installed>();
            if (!Directory.Exists(versionsDir)) return list;
            foreach (var dir in Directory.GetDirectories(versionsDir))
            {
                var name = Path.GetFileName(dir);
                var m = FolderPattern.Match(name);
                if (!m.Success) continue;
                var exe = Path.Combine(dir, name + ".exe");
                if (!File.Exists(exe)) continue;
                list.Add(new Installed
                {
                    Version = new Version(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value), int.Parse(m.Groups[3].Value)),
                    Dir = dir,
                    Exe = exe
                });
            }
            return list.OrderByDescending(i => i.Version).ToList();
        }

        private static void Start(Installed v, string[] args)
        {
            var psi = new ProcessStartInfo(v.Exe)
            {
                WorkingDirectory = v.Dir,
                UseShellExecute = false,
                Arguments = string.Join(" ", args.Select(Quote))
            };
            using (Process.Start(psi)) { }
        }

        private static string Quote(string a)
        {
            return a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0 ? a : "\"" + a.Replace("\"", "\\\"") + "\"";
        }

        /// <summary>A small picker in the app's dark palette.</summary>
        private static Installed Choose(List<Installed> installed)
        {
            var bg = Color.FromArgb(0x0E, 0x11, 0x16);
            var panel = Color.FromArgb(0x16, 0x1B, 0x22);
            var text = Color.FromArgb(0xD9, 0xE0, 0xEA);
            var dim = Color.FromArgb(0xAE, 0xB9, 0xC7);
            var accent = Color.FromArgb(0x5A, 0xB0, 0xF0);

            using (var form = new Form())
            {
                form.Text = Title + " — choose a version";
                form.FormBorderStyle = FormBorderStyle.FixedDialog;
                form.StartPosition = FormStartPosition.CenterScreen;
                form.MaximizeBox = false;
                form.MinimizeBox = false;
                form.ClientSize = new Size(380, 260);
                form.BackColor = bg;
                form.ForeColor = text;
                form.Font = new Font("Segoe UI", 10f);
                try { form.Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); } catch { }

                var label = new Label { Text = "Installed versions", ForeColor = dim, Left = 16, Top = 14, Width = 340 };
                var list = new ListBox
                {
                    Left = 16, Top = 40, Width = 348, Height = 160,
                    BackColor = panel, ForeColor = text, BorderStyle = BorderStyle.FixedSingle,
                    Font = new Font("Segoe UI", 10.5f)
                };
                for (var i = 0; i < installed.Count; i++)
                {
                    list.Items.Add(installed[i].Version + (i == 0 ? "   newest" : ""));
                }
                list.SelectedIndex = 0;

                var start = new Button
                {
                    Text = "Start", Left = 264, Top = 214, Width = 100, Height = 32,
                    BackColor = accent, ForeColor = bg, FlatStyle = FlatStyle.Flat, DialogResult = DialogResult.OK
                };
                start.FlatAppearance.BorderSize = 0;
                var cancel = new Button
                {
                    Text = "Cancel", Left = 152, Top = 214, Width = 100, Height = 32,
                    BackColor = panel, ForeColor = text, FlatStyle = FlatStyle.Flat, DialogResult = DialogResult.Cancel
                };
                list.DoubleClick += delegate { form.DialogResult = DialogResult.OK; form.Close(); };

                form.Controls.AddRange(new Control[] { label, list, start, cancel });
                form.AcceptButton = start;
                form.CancelButton = cancel;
                return form.ShowDialog() == DialogResult.OK && list.SelectedIndex >= 0 ? installed[list.SelectedIndex] : null;
            }
        }

        /// <summary>Appends to launcher.log beside the launcher; keeps it small; never fails the start.</summary>
        private static void Log(string line)
        {
            try
            {
                var path = Path.Combine(_root, "launcher.log");
                if (File.Exists(path) && new FileInfo(path).Length > 64 * 1024)
                {
                    var keep = File.ReadAllLines(path).Reverse().Take(200).Reverse().ToArray();
                    File.WriteAllLines(path, keep, Encoding.UTF8);
                }
                File.AppendAllText(path, DateTime.Now.ToString("dd-MM-yyyy HH:mm:ss") + "  " + line + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
                // A read-only folder must not stop the app from starting.
            }
        }
    }
}
