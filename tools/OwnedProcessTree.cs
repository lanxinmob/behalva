using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace PersonalAssistant
{
    // Runs one process inside a private Windows job object. The process is
    // created suspended and joins the job before it runs, so every descendant
    // (venv launcher child, Playwright driver, Chromium) is in the job, even
    // after its parent exits. Ending the job ends exactly that tree; closing
    // the last job handle, including when the owning PowerShell dies, does too.
    // Must compile as C# 5 for Windows PowerShell 5.1 Add-Type.
    public sealed class OwnedProcessTree : IDisposable
    {
        const uint CREATE_SUSPENDED = 0x00000004;
        const uint CREATE_NEW_CONSOLE = 0x00000010;
        const uint STARTF_USESHOWWINDOW = 0x00000001;
        const short SW_HIDE = 0;
        const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        const int JobObjectBasicAccountingInformation = 1;
        const int JobObjectExtendedLimitInformation = 9;
        const uint WAIT_OBJECT_0 = 0;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct STARTUPINFO
        {
            public int cb;
            public string lpReserved, lpDesktop, lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute;
            public uint dwFlags;
            public short wShowWindow, cbReserved2;
            public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct PROCESS_INFORMATION
        {
            public IntPtr hProcess, hThread;
            public int dwProcessId, dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IO_COUNTERS
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct EXTENDED_LIMIT_INFORMATION
        {
            public BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct BASIC_ACCOUNTING_INFORMATION
        {
            public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime;
            public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref EXTENDED_LIMIT_INFORMATION info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool QueryInformationJobObject(IntPtr job, int infoClass, out BASIC_ACCOUNTING_INFORMATION info, uint length, IntPtr returnLength);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes,
            IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
            ref STARTUPINFO startup, out PROCESS_INFORMATION info);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll")]
        static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll")]
        static extern bool CloseHandle(IntPtr handle);

        IntPtr job;
        IntPtr process;
        readonly int id;

        OwnedProcessTree(IntPtr job, IntPtr process, int id)
        {
            this.job = job;
            this.process = process;
            this.id = id;
        }

        public int Id { get { return id; } }

        // Root process only; descendants may outlive it until Dispose.
        public bool HasExited
        {
            get { return process == IntPtr.Zero || WaitForSingleObject(process, 0) == WAIT_OBJECT_0; }
        }

        public int ActiveProcesses
        {
            get
            {
                if (job == IntPtr.Zero) return 0;
                BASIC_ACCOUNTING_INFORMATION info;
                if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation, out info,
                        (uint)Marshal.SizeOf(typeof(BASIC_ACCOUNTING_INFORMATION)), IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return (int)info.ActiveProcesses;
            }
        }

        // Starts hidden like Start-Process -WindowStyle Hidden. commandLine
        // must already contain the quoted executable as its first token.
        public static OwnedProcessTree Start(string application, string commandLine)
        {
            IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            PROCESS_INFORMATION info = new PROCESS_INFORMATION();
            try
            {
                EXTENDED_LIMIT_INFORMATION limit = new EXTENDED_LIMIT_INFORMATION();
                limit.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref limit,
                        (uint)Marshal.SizeOf(typeof(EXTENDED_LIMIT_INFORMATION))))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                STARTUPINFO startup = new STARTUPINFO();
                startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
                startup.dwFlags = STARTF_USESHOWWINDOW;
                startup.wShowWindow = SW_HIDE;
                if (!CreateProcessW(application, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, false,
                        CREATE_SUSPENDED | CREATE_NEW_CONSOLE, IntPtr.Zero, null, ref startup, out info))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                if (!AssignProcessToJobObject(job, info.hProcess))
                {
                    int error = Marshal.GetLastWin32Error();
                    TerminateProcess(info.hProcess, 1);
                    throw new Win32Exception(error);
                }
                if (ResumeThread(info.hThread) == uint.MaxValue)
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                CloseHandle(info.hThread);
                return new OwnedProcessTree(job, info.hProcess, info.dwProcessId);
            }
            catch
            {
                TerminateJobObject(job, 1);
                if (info.hThread != IntPtr.Zero) CloseHandle(info.hThread);
                if (info.hProcess != IntPtr.Zero) CloseHandle(info.hProcess);
                CloseHandle(job);
                throw;
            }
        }

        // Ends every process in the tree and waits until the job is empty, so
        // a respawned bridge does not race the old one for the browser profile.
        public bool Stop(int timeoutMilliseconds)
        {
            if (job == IntPtr.Zero) return true;
            TerminateJobObject(job, 1);
            int waited = 0;
            while (ActiveProcesses > 0)
            {
                if (waited >= timeoutMilliseconds) return false;
                Thread.Sleep(50);
                waited += 50;
            }
            return true;
        }

        public void Dispose()
        {
            if (job == IntPtr.Zero) return;
            try { Stop(10000); }
            finally
            {
                CloseHandle(process);
                CloseHandle(job);
                process = IntPtr.Zero;
                job = IntPtr.Zero;
            }
        }
    }
}
