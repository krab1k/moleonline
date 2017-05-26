using System;

namespace Mole.API.Models
{
    public static class ComputationStatus
    {
        public const string Aborted = "Aborted";
        public const string Initialized = "Initialized";
        public const string Initializing = "Initializing";
        public const string Running = "Running";
        public const string Finished = "Finished";
        public const string Error = "Error";
        public const string Failed = "Failed";
        public const string Deleted = "Deleted";
        public const string FailedInitialization = "FailedInitialization";
    }
}
