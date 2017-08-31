using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace Mole.API
{
    public class Config
    {
        public string WorkingDirectory { get; set; }
        public string CSA { get; set; }
        public int MaxConcurentComputations { get; set; }
        public string MOLE { get; set; }
        public string MEMBED { get; set; }
        public string Pores { get; set; }
        public string PyMOL { get; set; }
    }
}
